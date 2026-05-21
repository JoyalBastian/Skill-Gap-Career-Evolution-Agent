from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("questionnaire", "0002_aiquestion_aianswer"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="aianswer",
            name="embedding",
        ),
        migrations.RemoveField(
            model_name="aianswer",
            name="nlp_entities",
        ),
        migrations.AlterField(
            model_name="aiquestion",
            name="source",
            field=models.CharField(default="gemini", max_length=20),
        ),
        # Drop legacy static questionnaire models.
        # QuestionnaireAnswer references Question, QuestionOption, QuestionnaireSession.
        # QuestionOption references Question. Question references QuestionCategory.
        # Delete in dependency-safe order.
        migrations.DeleteModel(name="QuestionnaireAnswer"),
        migrations.DeleteModel(name="QuestionOption"),
        migrations.DeleteModel(name="Question"),
        migrations.DeleteModel(name="QuestionCategory"),
    ]
