"""
Gemini-only AI interview question generator.

Questions are generated via the configured AI provider (Gemini or Ollama).
If the provider cannot be reached
or returns malformed JSON, the caller (QuestionnaireService) is expected to
surface a clear error to the user.
"""
from __future__ import annotations

from ai_engine.llm_client import GeminiUnavailable, chat_json

MAX_QUESTIONS = 12

_DEFAULT_CHOICE_OPTIONS = [
    "Strongly agree",
    "Somewhat agree",
    "Neutral",
    "Somewhat disagree",
    "Strongly disagree",
]


def _build_prompt(
    conversation_history: list[dict],
    question_number: int,
    resume_context: dict | None = None,
    topics_covered: list[str] | None = None,
) -> str:
    history_text = ""
    last_answer = ""
    for turn in conversation_history:
        history_text += f"Q: {turn.get('question', '')}\nA: {turn.get('answer', '')}\n\n"
        if turn.get("answer"):
            last_answer = turn.get("answer", "")

    topics_section = ""
    if topics_covered:
        topics_section = (
            "Topics already covered (DO NOT reuse these topic slugs):\n"
            + ", ".join(topics_covered)
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
            f"- Skills: {', '.join(resume_context.get('skills', [])[:10])}\n"
            f"- Education: {edu or 'unknown'}\n"
            f"- Certifications: {', '.join(resume_context.get('certifications', []))}\n"
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
            f"The user's last answer was: \"{last_answer[:500]}\"\n"
            "Your next question MUST build on that answer — reference a specific detail from it.\n\n"
        )

    prep_section = _interview_prep_section(resume_context)

    return (
        "You are an AI career counselor conducting a personalized career assessment interview.\n\n"
        f"{prep_section}{resume_section}{topics_section}{last_answer_hint}"
        f"Conversation so far:\n{history_text}"
        f"This is question number {question_number} of approximately {MAX_QUESTIONS}.\n\n"
        "Generate the NEXT most insightful question. Rules:\n"
        "- Ask ONE focused question\n"
        "- question_type MUST be \"single_choice\" or \"multi_choice\" (NEVER free_text)\n"
        "- options MUST contain 3-6 concise, mutually distinct answer choices\n"
        "- Do NOT repeat topics already covered\n"
        "- Build on the user's most recent answer\n"
        "- If enough context is gathered, set \"is_final\": true\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        "  \"text\": \"Your question here?\",\n"
        "  \"question_type\": \"single_choice\" | \"multi_choice\",\n"
        "  \"options\": [\"option1\", \"option2\", \"option3\"],\n"
        "  \"topic\": \"new_topic_slug\",\n"
        "  \"is_final\": false\n"
        "}"
    )


def _interview_prep_section(resume_context: dict | None) -> str:
    prep = (resume_context or {}).get("interview_prep") or {}
    if not prep:
        return ""
    lines = ["User provided this information before the interview:"]
    if prep.get("career_goals"):
        lines.append(f"- Career goals: {prep['career_goals']}")
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
            f"{prep_section}"
            f"User first name: {first_name}\n"
            f"Current title from resume: {title}\n"
            "This is the ONLY free-text question in the interview. Ask for a detailed open response about career goals, background, and what they want to achieve. "
            "Use the pre-interview information above — do not re-ask what they already shared.\n\n"
            "Respond ONLY with valid JSON:\n"
            "{\n"
            "  \"text\": \"Your question here?\",\n"
            "  \"question_type\": \"free_text\",\n"
            "  \"options\": [],\n"
            "  \"topic\": \"career_goals\",\n"
            "  \"is_final\": false\n"
            "}"
        )
    return (
        "You are an AI career counselor. Ask the FIRST open question to a new user about their interests, hobbies and what work excites them.\n"
        f"{prep_section}"
        "This is the ONLY free-text question in the interview. Use any pre-interview information above — do not re-ask what they already shared.\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        "  \"text\": \"Your question here?\",\n"
        "  \"question_type\": \"free_text\",\n"
        "  \"options\": [],\n"
        "  \"topic\": \"introduction\",\n"
        "  \"is_final\": false\n"
        "}"
    )


def _coerce_choice_options(options: list, question_type: str) -> tuple[str, list]:
    """Ensure Q2+ has valid choice type and 3-6 options."""
    qtype = question_type if question_type in ("single_choice", "multi_choice") else "single_choice"
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    if len(opts) < 3:
        opts = list(_DEFAULT_CHOICE_OPTIONS)
    elif len(opts) > 6:
        opts = opts[:6]
    return qtype, opts


def _normalize(
    data: dict,
    default_topic: str = "general",
    question_number: int = 1,
) -> dict | None:
    if not isinstance(data, dict) or not data.get("text"):
        return None

    qtype = data.get("question_type", "free_text")
    options = data.get("options", []) or []

    if question_number > 1:
        qtype, options = _coerce_choice_options(options, qtype)
    elif qtype != "free_text":
        qtype = "free_text"
        options = []

    return {
        "text": data["text"],
        "question_type": qtype,
        "options": options,
        "topic": data.get("topic", default_topic),
        "is_final": bool(data.get("is_final", False)),
        "source": "gemini",
    }


def get_first_question(resume_context: dict | None = None) -> dict:
    """Return the opening question. Raises GeminiUnavailable on failure."""
    prompt = _build_first_prompt(resume_context)
    data = chat_json(prompt, temperature=0.5)
    normalized = _normalize(data, default_topic="introduction", question_number=1)
    if not normalized:
        raise GeminiUnavailable("Gemini did not return a valid first question.")
    return normalized


def get_next_question(
    conversation_history: list[dict],
    question_number: int,
    resume_context: dict | None = None,
    topics_covered: list[str] | None = None,
) -> dict | None:
    """Return the next question, or None if Gemini signals the session is final."""
    if question_number > MAX_QUESTIONS:
        return None

    prompt = _build_prompt(
        conversation_history,
        question_number,
        resume_context,
        topics_covered=topics_covered,
    )
    data = chat_json(prompt, temperature=0.5)
    normalized = _normalize(data, question_number=question_number)
    if not normalized:
        raise GeminiUnavailable("Gemini did not return a valid next question.")
    if normalized["is_final"]:
        return None
    return normalized


def should_end_session(conversation_history: list[dict], question_number: int) -> bool:
    if question_number > MAX_QUESTIONS:
        return True
    meaningful = sum(1 for t in conversation_history if len(t.get("answer", "").strip()) > 10)
    return meaningful >= MAX_QUESTIONS
