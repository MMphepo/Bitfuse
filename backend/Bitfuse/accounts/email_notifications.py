from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction


def notify_admin(subject, message):
    transaction.on_commit(
        lambda: send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [settings.ADMIN_NOTIFICATION_EMAIL],
            fail_silently=False,
        ),
        robust=True,
    )
