from django.db import migrations
from django.core.management import call_command


def seed_users_on_build(apps, schema_editor):
    try:
        call_command("seed_users")
    except Exception as e:
        # We catch exceptions to prevent build/migrate from breaking if there are transient database setup/networking issues
        print(f"Warning: Seed users on build failed: {str(e)}")


class Migration(migrations.Migration):

    dependencies = [
        ("withdrawals", "0001_initial"),
        ("accounts", "0005_notification"),
        ("kyc", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_users_on_build, reverse_code=migrations.RunPython.noop),
    ]
