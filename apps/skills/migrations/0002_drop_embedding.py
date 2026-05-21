from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("skills", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="skill",
            name="embedding_vector",
        ),
    ]
