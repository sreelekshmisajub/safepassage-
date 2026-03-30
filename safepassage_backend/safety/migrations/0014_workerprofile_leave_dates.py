from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("safety", "0013_alter_checkin_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="workerprofile",
            name="leave_dates",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
