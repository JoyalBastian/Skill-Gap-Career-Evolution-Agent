from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommendations", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="learningresource",
            name="embedding_vector",
        ),
        migrations.AddField(
            model_name="learningresource",
            name="is_ai_generated",
            field=models.BooleanField(default=True),
        ),
    ]
