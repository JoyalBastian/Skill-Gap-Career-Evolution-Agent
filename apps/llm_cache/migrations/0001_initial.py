from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="LLMCacheEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("prompt_preview", models.TextField(blank=True)),
                ("response_json", models.JSONField(blank=True, default=dict)),
                ("response_text", models.TextField(blank=True)),
                ("model_name", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
