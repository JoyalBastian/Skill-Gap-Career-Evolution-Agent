def site_settings(request):
    """Inject site-wide context into every template."""
    from django.conf import settings

    from ai_engine.llm_client import active_provider, is_enabled

    enabled = is_enabled()
    return {
        "SITE_NAME": settings.SITE_NAME,
        "CHATBOT_ENABLED": enabled,
        "GEMINI_ENABLED": enabled,
        "AI_PROVIDER": active_provider(),
    }
