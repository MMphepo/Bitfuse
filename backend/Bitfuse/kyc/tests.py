from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient
from unittest import mock

from .models import KYCSubmission

User = get_user_model()


class KYCSubmissionEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="kyc-user",
            email="kyc-user@example.com",
            password="pw",
            phone_number="+265991000000",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def upload_files(self):
        return {
            field: SimpleUploadedFile(f"{field}.jpg", b"test image", content_type="image/jpeg")
            for field in ("id_front", "id_back", "selfie")
        }

    @mock.patch("accounts.email_notifications.send_mail")
    @mock.patch("django.core.files.storage.FileSystemStorage._save", return_value="test-file.jpg")
    def test_new_and_resubmitted_kyc_notify_admin(self, _save_file, send_mail):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("kyc-submit"), self.upload_files(), format="multipart"
            )

        self.assertEqual(response.status_code, 201, response.data)
        send_mail.assert_called_once()
        self.assertEqual(send_mail.call_args.args[3], ["maathmphepo80@gmail.com"])
        self.assertIn("KYC submission received", send_mail.call_args.args[0])
        self.assertNotIn("test image", send_mail.call_args.args[1])

        submission = KYCSubmission.objects.get(user=self.user)
        submission.status = "rejected"
        submission.save(update_fields=["status"])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("kyc-submit"), self.upload_files(), format="multipart"
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(send_mail.call_count, 2)
