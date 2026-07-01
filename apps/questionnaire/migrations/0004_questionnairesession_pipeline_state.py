from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("questionnaire", "0003_drop_legacy"),
    ]

    operations = [
        migrations.AddField(
            model_name="questionnairesession",
            name="pipeline_state",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
