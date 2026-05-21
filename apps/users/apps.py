from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    label = "users"

    def ready(self):
        from core.sqlite import setup_sqlite

        setup_sqlite()
        from . import signals  # noqa: F401
