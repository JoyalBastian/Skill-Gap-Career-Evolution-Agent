import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TrendingJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("summary", models.TextField(blank=True)),
                ("demand_label", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("very_high", "Very High")], default="medium", max_length=20)),
                ("growth_reason", models.TextField(blank=True)),
                ("required_skills", models.JSONField(blank=True, default=list)),
                ("suggested_titles", models.JSONField(blank=True, default=list)),
                ("salary_band_text", models.CharField(blank=True, max_length=120)),
                ("refreshed_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-refreshed_at"]},
        ),
        migrations.CreateModel(
            name="JobMatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fit_score", models.FloatField(default=0.0)),
                ("fit_reason", models.TextField(blank=True)),
                ("matched_skills", models.JSONField(blank=True, default=list)),
                ("missing_skills", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="matches", to="jobs.trendingjob")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_matches", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-fit_score"], "unique_together": {("user", "job")}},
        ),
    ]
