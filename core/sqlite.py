"""SQLite tuning and retry helpers to reduce 'database is locked' errors."""
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, TypeVar

from django.db import OperationalError
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Milliseconds SQLite will wait for a lock before failing.
SQLITE_BUSY_TIMEOUT_MS = 60_000

# Set True after we attempt WAL once (success or give up) for this process.
_wal_setup_done = False


def configure_sqlite_connection(sender, connection, **kwargs):
    """
    Apply SQLite PRAGMAs when Django opens a connection.

    Must not raise here — a failure would prevent runserver/migrations from starting.
    """
    global _wal_setup_done

    if connection.vendor != "sqlite":
        return

    try:
        with connection.cursor() as cursor:
            # Always set first so later PRAGMAs wait instead of failing instantly.
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")

            if not _wal_setup_done:
                for attempt in range(15):
                    try:
                        cursor.execute("PRAGMA journal_mode=WAL;")
                        mode = cursor.fetchone()
                        logger.info(
                            "SQLite WAL enabled (journal_mode=%s)",
                            mode[0] if mode else "wal",
                        )
                        _wal_setup_done = True
                        break
                    except OperationalError as exc:
                        if attempt >= 14:
                            logger.warning(
                                "SQLite WAL not enabled (%s). "
                                "Close other apps using db.sqlite3 (old runserver, DB Browser) "
                                "and restart. Server will continue with default journaling.",
                                exc,
                            )
                            _wal_setup_done = True
                        else:
                            time.sleep(0.25 * (attempt + 1))

            if _wal_setup_done:
                try:
                    cursor.execute("PRAGMA synchronous=NORMAL;")
                except OperationalError:
                    pass

    except OperationalError as exc:
        # Never crash Django startup because of PRAGMA setup.
        logger.warning("SQLite PRAGMA setup skipped (%s).", exc)


def setup_sqlite():
    """Register the connection hook once at startup."""
    connection_created.connect(
        configure_sqlite_connection,
        dispatch_uid="skillgap_sqlite_wal",
    )


def db_retry(
    *,
    attempts: int = 5,
    base_delay: float = 0.15,
    max_delay: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry a function on SQLite OperationalError (database is locked)."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exc: OperationalError | None = None
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    err = str(e).lower()
                    if "locked" not in err and "busy" not in err:
                        raise
                    last_exc = e
                    if attempt >= attempts - 1:
                        raise
                    delay = min(base_delay * (2**attempt), max_delay)
                    logger.warning(
                        "SQLite lock on %s (attempt %s/%s), retrying in %.2fs",
                        func.__name__,
                        attempt + 1,
                        attempts,
                        delay,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
