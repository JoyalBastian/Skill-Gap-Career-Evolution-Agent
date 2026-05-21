"""Root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.users.urls")),
    path("accounts/", include("apps.authentication.urls")),
    path("questionnaire/", include("apps.questionnaire.urls")),
    path("careers/", include("apps.careers.urls")),
    path("skills/", include("apps.skills.urls")),
    path("recommendations/", include("apps.recommendations.urls")),
    path("roadmap/", include("apps.roadmap.urls")),
    path("progress/", include("apps.progress.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("jobs/", include("apps.jobs.urls")),
    path("chat/", RedirectView.as_view(pattern_name="analytics:chat", permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
