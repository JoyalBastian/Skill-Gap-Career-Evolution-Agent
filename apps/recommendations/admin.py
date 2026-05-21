from django.contrib import admin

from .models import LearningResource, Recommendation


admin.site.register(LearningResource)
admin.site.register(Recommendation)
