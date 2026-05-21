from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("careers", "0002_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="careerdomain",
            name="domain_embedding",
        ),
        migrations.RemoveField(
            model_name="careerdomain",
            name="required_traits",
        ),
        migrations.AlterField(
            model_name="careerprediction",
            name="model_version",
            field=models.CharField(default="gemini-v1", max_length=50),
        ),
    ]
