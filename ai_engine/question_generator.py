"""
Gemini-only AI interview question generator.

Questions are generated via the configured AI provider (Gemini or Ollama).
If the provider cannot be reached
or returns malformed JSON, the caller (QuestionnaireService) is expected to
surface a clear error to the user.
"""
from __future__ import annotations

import json
import re

from ai_engine.llm_client import GeminiUnavailable, chat_json
from ai_engine.llm_common import strip_json_fences

MAX_QUESTIONS = 7
RECENT_HISTORY_TURNS = 3
_QUESTION_MAX_TOKENS = 384

_DEFAULT_CHOICE_OPTIONS = [
    "Strongly agree",
    "Somewhat agree",
    "Neutral",
    "Somewhat disagree",
    "Strongly disagree",
]

# Values copied verbatim from prompt examples by smaller local models (e.g. Ollama).
_PLACEHOLDER_QUESTION_TEXTS = frozenset({
    "your question here?",
    "your question here",
})
_PLACEHOLDER_TOPICS = frozenset({
    "new_topic_slug",
    "topic_slug",
})
_PLACEHOLDER_OPTION_PREFIXES = ("option1", "option2", "option3", "option4", "option5", "option6")

_ENGLISH_ONLY = (
    "Write the question text, every option, and the topic in English ONLY. "
    "Do not use any other language or non-Latin characters.\n"
)

_NON_ENGLISH_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
    r"\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u0e00-\u0e7f\uff00-\uffef]"
)

_JSON_SCHEMA_NEXT = (
    "Respond ONLY with valid JSON. Replace every <...> placeholder with real content "
    "(never echo the placeholder text):\n"
    "{\n"
    '  "text": "<one clear interview question ending with ?>",\n'
    '  "question_type": "single_choice",\n'
    '  "options": ["<short choice A>", "<short choice B>", "<short choice C>"],\n'
    '  "topic": "<snake_case_topic e.g. work_preferences>",\n'
    '  "is_final": false\n'
    "}"
)

_JSON_SCHEMA_FIRST = (
    "Respond ONLY with valid JSON. Replace every <...> placeholder with real content "
    "(never echo the placeholder text):\n"
    "{\n"
    '  "text": "<warm greeting and one open question about career goals>",\n'
    '  "question_type": "free_text",\n'
    '  "options": [],\n'
    '  "topic": "career_goals",\n'
    '  "is_final": false\n'
    "}"
)

_JSON_SCHEMA_FIRST_NO_RESUME = (
    "Respond ONLY with valid JSON. Replace every <...> placeholder with real content "
    "(never echo the placeholder text):\n"
    "{\n"
    '  "text": "<friendly open question about interests and career aspirations>",\n'
    '  "question_type": "free_text",\n'
    '  "options": [],\n'
    '  "topic": "introduction",\n'
    '  "is_final": false\n'
    "}"
)


def _build_prompt(
    conversation_history: list[dict],
    question_number: int,
    resume_context: dict | None = None,
    topics_covered: list[str] | None = None,
    questions_asked: list[str] | None = None,
) -> str:
    history_text = ""
    last_answer = ""
    recent = conversation_history[-RECENT_HISTORY_TURNS:] if conversation_history else []
    for turn in recent:
        history_text += f"Q: {turn.get('question', '')[:200]}\nA: {turn.get('answer', '')[:200]}\n\n"
        if turn.get("answer"):
            last_answer = turn.get("answer", "")

    topics_section = ""
    if topics_covered:
        topics_section = (
            "Topics already covered (DO NOT reuse these topic slugs):\n"
            + ", ".join(topics_covered)
            + "\n\n"
        )

    asked_section = ""
    if questions_asked:
        asked_section = (
            "Questions already asked (DO NOT repeat or rephrase these):\n"
            + "\n".join(f"- {q[:180]}" for q in questions_asked[-6:])
            + "\n\n"
        )

    resume_section = ""
    if resume_context:
        edu = ", ".join(
            f"{e.get('degree', '')} {e.get('field', '')}".strip()
            for e in resume_context.get("education", [])
        )
        resume_section = (
            "User's Resume Data (already known — DO NOT ask about these):\n"
            f"- Name: {resume_context.get('full_name', 'unknown')}\n"
            f"- Current Title: {resume_context.get('current_title', 'unknown')}\n"
            f"- Experience: {resume_context.get('experience_years', 0)} years ({resume_context.get('experience_level', 'unknown')} level)\n"
            f"- Skills: {', '.join(resume_context.get('skills', [])[:6])}\n"
            f"- Education: {edu or 'unknown'}\n"
            f"- Certifications: {', '.join(resume_context.get('certifications', [])[:3])}\n"
            f"- Career Domain: {resume_context.get('career_domain', 'unknown')}\n\n"
            "Focus ONLY on:\n"
            "- Career goals and aspirations\n"
            "- Soft skills and personality traits\n"
            "- Work preferences and environment\n"
            "- What they want to learn or improve\n\n"
        )

    last_answer_hint = ""
    if last_answer:
        last_answer_hint = (
            f"The user's last answer was: \"{last_answer[:200]}\"\n"
            "Your next question MUST build on that answer — reference a specific detail from it.\n\n"
        )

    prep_section = _interview_prep_section(resume_context)

    return (
        "You are an AI career counselor conducting a personalized career assessment interview.\n\n"
        f"{prep_section}{resume_section}{topics_section}{asked_section}{last_answer_hint}"
        f"Conversation so far:\n{history_text}"
        f"This is question number {question_number} of approximately {MAX_QUESTIONS}.\n\n"
        f"{_ENGLISH_ONLY}"
        "Generate the NEXT most insightful question. Rules:\n"
        "- Ask ONE focused question\n"
        "- question_type MUST be \"single_choice\" or \"multi_choice\" (NEVER free_text)\n"
        "- options MUST contain 3-6 concise, mutually distinct answer choices\n"
        "- Do NOT repeat topics already covered\n"
        "- Build on the user's most recent answer\n"
        "- If enough context is gathered, set \"is_final\": true\n"
        "- options MUST be a JSON array of plain strings only (NOT objects with label/value)\n"
        "- options must be short labels (under 55 characters), not full sentences\n"
        "- topic must be a real snake_case label (e.g. leadership_style), not a placeholder\n\n"
        f"{_JSON_SCHEMA_NEXT}"
    )


def _interview_prep_section(resume_context: dict | None) -> str:
    prep = (resume_context or {}).get("interview_prep") or {}
    if not prep:
        return ""
    lines = ["User provided this information before the interview:"]
    if prep.get("career_goals"):
        lines.append(f"- Career goals: {prep['career_goals'][:300]}")
    if prep.get("focus_areas"):
        lines.append(f"- Areas to explore: {prep['focus_areas']}")
    if prep.get("current_title"):
        lines.append(f"- Current role: {prep['current_title']}")
    if prep.get("years_experience"):
        lines.append(f"- Years of experience: {prep['years_experience']}")
    level = (resume_context or {}).get("target_career_level")
    if level:
        lines.append(f"- Target level: {level}")
    track = (resume_context or {}).get("is_technical_track")
    if track is not None:
        lines.append(f"- Track: {'technical' if track else 'non-technical'}")
    return "\n".join(lines) + "\n\n"


def _build_first_prompt(resume_context: dict | None) -> str:
    rc = resume_context or {}
    prep_section = _interview_prep_section(resume_context)
    if rc.get("full_name"):
        first_name = rc["full_name"].split()[0] if rc["full_name"] else ""
        title = rc.get("current_title", "")
        return (
            "You are an AI career counselor. Greet the user by their first name and ask the FIRST interview question.\n\n"
            f"{_ENGLISH_ONLY}"
            f"{prep_section}"
            f"User first name: {first_name}\n"
            f"Current title from resume: {title}\n"
            "This is the ONLY free-text question in the interview. Ask for a detailed open response about career goals, background, and what they want to achieve. "
            "Use the pre-interview information above — do not re-ask what they already shared.\n\n"
            f"{_JSON_SCHEMA_FIRST}"
        )
    return (
        "You are an AI career counselor. Ask the FIRST open question to a new user about their interests, hobbies and what work excites them.\n"
        f"{_ENGLISH_ONLY}"
        f"{prep_section}"
        "This is the ONLY free-text question in the interview. Use any pre-interview information above — do not re-ask what they already shared.\n\n"
        f"{_JSON_SCHEMA_FIRST_NO_RESUME}"
    )


def _contains_non_english(text: str) -> bool:
    return bool(_NON_ENGLISH_RE.search(text or ""))


def _output_has_non_english(text: str, options: list[str], topic: str = "") -> bool:
    if _contains_non_english(text):
        return True
    if topic and _contains_non_english(topic):
        return True
    return any(_contains_non_english(opt) for opt in options)


def _raw_data_is_non_english(data: dict) -> bool:
    """True when repaired LLM output contains non-English scripts in text or options."""
    if not isinstance(data, dict):
        return False
    repaired = _try_repair_data_from_json_text(data)
    if repaired is None or not repaired.get("text"):
        return False
    text = str(repaired["text"]).strip()
    options = _sanitize_options(repaired.get("options") or [])
    topic = str(repaired.get("topic") or "").strip()
    return _output_has_non_english(text, options, topic)


def _text_looks_like_json(text: str) -> bool:
    """True when question text is likely a raw JSON blob, not a human question."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped[0] in "{[":
        return True
    lowered = stripped.lower()
    return '"question_type"' in lowered or '"is_final"' in lowered


def _merge_question_fields(base: dict, inner: dict) -> dict:
    merged = dict(base)
    for key in ("text", "question_type", "options", "topic", "is_final"):
        if key in inner and inner[key] is not None:
            merged[key] = inner[key]
    return merged


def _parse_malformed_question_json(text: str) -> dict | None:
    """
    Repair JSON where the model used the question string as an anonymous first key:
    {"Hello there?","question_type": "free_text", ...}
    """
    match = re.match(
        r'^\{\s*"((?:[^"\\]|\\.)+)"\s*,\s*',
        text.strip(),
        re.DOTALL,
    )
    if not match:
        return None
    question_text = match.group(1).replace('\\"', '"').strip()
    if not question_text:
        return None
    remainder = text.strip()[match.end():].lstrip(",").strip()
    fixed = '{"text": ' + json.dumps(question_text) + ", " + remainder
    if not fixed.rstrip().endswith("}"):
        fixed = fixed.rstrip().rstrip(",") + "}"
    try:
        parsed = json.loads(fixed)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _try_repair_data_from_json_text(data: dict) -> dict | None:
    """Lift nested or malformed JSON out of the text field when the model mis-formats output."""
    text = (data.get("text") or "").strip()
    if not _text_looks_like_json(text):
        return data

    for candidate in (text, strip_json_fences(text)):
        if not candidate:
            continue
        try:
            inner = json.loads(candidate)
        except json.JSONDecodeError:
            inner = None
        if isinstance(inner, dict) and (inner.get("text") or "").strip():
            return _merge_question_fields(data, inner)

    malformed = _parse_malformed_question_json(text)
    if malformed and (malformed.get("text") or "").strip():
        return _merge_question_fields(data, malformed)

    return None


def _is_duplicate_question(text: str, questions_asked: list[str] | None) -> bool:
    if not questions_asked:
        return False
    normalized_new = text.strip().lower()
    if not normalized_new:
        return False
    for asked in questions_asked:
        if asked.strip().lower() == normalized_new:
            return True
    return False


def _looks_like_template_echo(data: dict) -> bool:
    """Detect when the model copied prompt examples instead of generating content."""
    text = (data.get("text") or "").strip().lower()
    if not text or text in _PLACEHOLDER_QUESTION_TEXTS:
        return True
    if "your question here" in text:
        return True

    topic = (data.get("topic") or "").strip().lower()
    if topic in _PLACEHOLDER_TOPICS or topic.startswith("<"):
        return True

    raw_opts = []
    for o in data.get("options") or []:
        flat = _flatten_option(o)
        if flat:
            raw_opts.append(flat)
    if raw_opts:
        lowered = [o.lower() for o in raw_opts]
        if all(any(o.startswith(prefix) for prefix in _PLACEHOLDER_OPTION_PREFIXES) for o in lowered[:3]):
            return True
        if any(o.startswith("<") and o.endswith(">") for o in raw_opts):
            return True
    return False


def _flatten_option(raw) -> str | None:
    """Normalize one option to a display string (handles dict-shaped LLM output)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        for key in ("label", "text", "title", "name", "value"):
            val = raw.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return None
    if isinstance(raw, (list, tuple, set)):
        return None
    text = str(raw).strip()
    if not text or text.startswith("<"):
        return None
    # Already stringified dict from a bad prior save — skip.
    if text.startswith("{") and "label" in text and "value" in text:
        return None
    return text


def normalize_option_list(options: list | None, *, min_options: int = 0) -> list[str]:
    """Flatten and sanitize options; optionally pad with defaults for broken legacy rows."""
    cleaned = _sanitize_options(options or [])
    if min_options and len(cleaned) < min_options:
        return list(_DEFAULT_CHOICE_OPTIONS)
    return cleaned


def _sanitize_options(options: list) -> list[str]:
    """Drop invalid choices; flatten dict-shaped options from the LLM."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options or []:
        opt = _flatten_option(raw)
        if not opt:
            continue
        if len(opt) > 55 or len(opt.split()) > 8:
            continue
        if opt.lower().startswith("option") and len(opt) <= 8:
            continue
        key = opt.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(opt)
    return cleaned


def _coerce_choice_options(options: list, question_type: str) -> tuple[str, list] | None:
    """Ensure Q2+ has valid choice type and 3-6 options. None => retry generation."""
    qtype = question_type if question_type in ("single_choice", "multi_choice") else "single_choice"
    opts = _sanitize_options(options)
    if len(opts) < 3:
        return None
    if len(opts) > 6:
        opts = opts[:6]
    return qtype, opts


def _normalize(
    data: dict,
    default_topic: str = "general",
    question_number: int = 1,
    *,
    allow_degrade: bool = False,
    questions_asked: list[str] | None = None,
) -> dict | None:
    if not isinstance(data, dict):
        return None

    repaired = _try_repair_data_from_json_text(data)
    if repaired is None:
        return None
    data = repaired

    if not data.get("text"):
        return None
    if _text_looks_like_json(str(data.get("text", ""))):
        return None
    if _looks_like_template_echo(data):
        return None

    text = str(data["text"]).strip()
    if _is_duplicate_question(text, questions_asked):
        return None

    qtype = data.get("question_type", "free_text")
    options = data.get("options", []) or []

    if question_number > 1:
        coerced = _coerce_choice_options(options, qtype)
        if coerced is None:
            if allow_degrade:
                qtype = "free_text"
                options = []
            else:
                return None
        else:
            qtype, options = coerced
    elif qtype != "free_text":
        qtype = "free_text"
        options = []

    topic = (data.get("topic") or default_topic).strip()
    if topic.lower() in _PLACEHOLDER_TOPICS or topic.startswith("<"):
        topic = default_topic

    if _output_has_non_english(text, options, topic):
        return None

    return {
        "text": text,
        "question_type": qtype,
        "options": options,
        "topic": topic,
        "is_final": bool(data.get("is_final", False)),
        "source": "gemini",
    }


def _generate_question(
    prompt: str,
    *,
    default_topic: str,
    question_number: int,
    retries: int = 2,
    questions_asked: list[str] | None = None,
) -> dict | None:
    """Call the LLM with retries when output is invalid. None => end session gracefully."""
    last_data: dict | None = None
    last_failure_duplicate = False
    last_failure_non_english = False
    for attempt in range(retries + 1):
        temperature = 0.4 if attempt == 0 else 0.6
        data = chat_json(
            prompt,
            temperature=temperature,
            max_output_tokens=_QUESTION_MAX_TOKENS,
        )
        last_data = data if isinstance(data, dict) else None
        normalized = _normalize(
            data,
            default_topic=default_topic,
            question_number=question_number,
            allow_degrade=attempt == retries,
            questions_asked=questions_asked,
        )
        if normalized:
            return normalized
        if last_data and _is_duplicate_question(
            str(last_data.get("text", "")),
            questions_asked,
        ):
            last_failure_duplicate = True
        if last_data and _raw_data_is_non_english(last_data):
            last_failure_non_english = True

    if last_failure_duplicate or last_failure_non_english:
        return None

    raise GeminiUnavailable(
        "AI returned a template placeholder instead of a real interview question."
        if last_data and _looks_like_template_echo(last_data)
        else "AI did not return a valid interview question."
    )


def get_first_question(resume_context: dict | None = None) -> dict:
    """Return the opening question. Raises GeminiUnavailable on failure."""
    prompt = _build_first_prompt(resume_context)
    default_topic = "career_goals" if (resume_context or {}).get("full_name") else "introduction"
    result = _generate_question(
        prompt,
        default_topic=default_topic,
        question_number=1,
    )
    if result is None:
        raise GeminiUnavailable("AI did not return a valid interview question.")
    return result


def get_next_question(
    conversation_history: list[dict],
    question_number: int,
    resume_context: dict | None = None,
    topics_covered: list[str] | None = None,
    questions_asked: list[str] | None = None,
) -> dict | None:
    """Return the next question, or None if the session should end."""
    if question_number > MAX_QUESTIONS:
        return None

    prompt = _build_prompt(
        conversation_history,
        question_number,
        resume_context,
        topics_covered=topics_covered,
        questions_asked=questions_asked,
    )
    normalized = _generate_question(
        prompt,
        default_topic="general",
        question_number=question_number,
        questions_asked=questions_asked,
    )
    if normalized is None:
        return None
    if normalized["is_final"]:
        return None
    return normalized


def should_end_session(conversation_history: list[dict], question_number: int) -> bool:
    if question_number > MAX_QUESTIONS:
        return True
    meaningful = sum(1 for t in conversation_history if len(t.get("answer", "").strip()) > 10)
    return meaningful >= MAX_QUESTIONS
