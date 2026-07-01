from django import template
from django.contrib.messages import get_messages

register = template.Library()


@register.inclusion_tag("partials/_messages.html", takes_context=True)
def show_messages(context):
    """Render flash messages once, deduplicated (at most one error banner)."""
    request = context.get("request")
    if not request:
        return {"items": []}

    seen_texts: set[str] = set()
    error_shown = False
    items: list[dict] = []

    for message in get_messages(request):
        text = str(message).strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)

        tags = message.tags or ""
        is_error = tags == "error" or "error" in tags.split()
        if is_error:
            if error_shown:
                continue
            error_shown = True

        items.append({"tags": tags, "text": text, "is_error": is_error})

    return {"items": items}
