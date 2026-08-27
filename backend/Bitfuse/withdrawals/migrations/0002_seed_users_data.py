from django.db import migrations
from django.core.management import call_command


def noop_seed(apps, schema_editor):
    # Seeding must be explicitly run via `python manage.py seed_users` command.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("withdrawals", "0001_initial"),
        ("accounts", "0005_notification"),
        ("kyc", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(noop_seed, reverse_code=migrations.RunPython.noop),
    ]
