from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_drop_catalogs"),
    ]

    operations = [
        migrations.AddField(
            model_name="resumeupload",
            name="error_message",
            field=models.TextField(blank=True, default=""),
        ),
    ]
