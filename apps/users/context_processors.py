from apps.users.services.journey_service import JourneyService


def journey(request):
    """Inject user journey state into every template."""
    if request.user.is_authenticated:
        return {"journey": JourneyService.get_for_request(request)}
    return {"journey": None}
