from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_profile_resume_context"),
    ]

    operations = [
        migrations.DeleteModel(name="UserInterest"),
        migrations.DeleteModel(name="UserPersonalityTrait"),
        migrations.DeleteModel(name="InterestCatalog"),
        migrations.DeleteModel(name="PersonalityTraitCatalog"),
    ]
