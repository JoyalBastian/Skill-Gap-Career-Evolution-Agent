from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.users.mixins import JourneyGatedViewMixin
from services.analytics_service import AnalyticsService
from services.chatbot_service import ChatbotService


class AnalyticsDashboardView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "analytics:dashboard"
    template_name = "analytics/dashboard.html"

    def get(self, request):
        analytics = AnalyticsService().get_dashboard_data(request.user.id)
        return render(request, self.template_name, {"analytics": analytics})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def chart_data_api(request):
    return JsonResponse(AnalyticsService().get_chart_json(request.user.id))


class ChatView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "analytics:chat"
    template_name = "analytics/chat.html"

    def get(self, request):
        chatbot = ChatbotService()
        return render(request, self.template_name, {
            "history": chatbot.get_history(request.user.id),
            "chatbot_enabled": chatbot.is_enabled(),
            "highlight_last": request.GET.get("highlight") == "1",
            "quick_prompts": [
                "Why am I matched to this career?",
                "What should I learn first?",
                "How do I close my skill gaps?",
                "What jobs can I apply for now?",
                "Give me a study plan for this week",
            ],
        })

    def post(self, request):
        message = request.POST.get("message", "").strip()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        chat_url = reverse("analytics:chat")

        if not message:
            if is_ajax:
                return JsonResponse({"error": "Please enter a message."}, status=400)
            messages.warning(request, "Please enter a message.")
            return redirect(chat_url)

        reply = ChatbotService().send_message(request.user.id, message)

        if is_ajax:
            return JsonResponse({"reply": reply})

        return redirect(f"{chat_url}?highlight=1")
