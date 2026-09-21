import io
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from kyc.models import KYCSubmission

User = get_user_model()


class KYCFlowAndAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="kyc_user",
            email="kyc@example.com",
            phone_number="+265999000111",
            password="ValidPassword123!",
            email_verified=True,
            phone_verified=True,
            verification_status="unverified",
        )
        self.admin = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            phone_number="+265999000222",
            password="AdminPassword123!",
        )

        # Create dummy image bytes for upload tests
        self.id_front = SimpleUploadedFile("id_front.jpg", b"dummy_id_front_bytes", content_type="image/jpeg")
        self.id_back = SimpleUploadedFile("id_back.jpg", b"dummy_id_back_bytes", content_type="image/jpeg")
        self.selfie = SimpleUploadedFile("selfie.jpg", b"dummy_selfie_bytes", content_type="image/jpeg")

    def test_kyc_submission_preserves_auth_and_sets_pending(self):
        self.client.force_authenticate(user=self.user)

        data = {
            "id_front": self.id_front,
            "id_back": self.id_back,
            "selfie": self.selfie,
        }
        resp = self.client.post("/api/v1/kyc/submit/", data, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["status"], "pending")

        # User remains logged in and status is pending
        self.user.refresh_from_db()
        self.assertEqual(self.user.verification_status, "pending")

        # Auth endpoint /me/ still succeeds
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_resp.status_code, status.HTTP_200_OK)

    def test_duplicate_pending_kyc_submission_returns_400(self):
        self.client.force_authenticate(user=self.user)

        KYCSubmission.objects.create(
            user=self.user,
            id_front="kyc/id_front/f.jpg",
            id_back="kyc/id_back/b.jpg",
            selfie="kyc/selfie/s.jpg",
            status="pending",
        )

        data = {
            "id_front": self.id_front,
            "id_back": self.id_back,
            "selfie": self.selfie,
        }
        resp = self.client.post("/api/v1/kyc/submit/", data, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)

    def test_kyc_resubmission_after_rejection(self):
        self.client.force_authenticate(user=self.user)

        # Create rejected submission
        sub = KYCSubmission.objects.create(
            user=self.user,
            id_front="kyc/id_front/f.jpg",
            id_back="kyc/id_back/b.jpg",
            selfie="kyc/selfie/s.jpg",
            status="rejected",
            rejection_reason="Blurry selfie",
        )
        self.user.verification_status = "rejected"
        self.user.save()

        data = {
            "id_front": self.id_front,
            "id_back": self.id_back,
            "selfie": self.selfie,
        }
        resp = self.client.post("/api/v1/kyc/submit/", data, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "pending")

        self.user.refresh_from_db()
        self.assertEqual(self.user.verification_status, "pending")

    def test_admin_approval_and_rejection_transitions(self):
        sub = KYCSubmission.objects.create(
            user=self.user,
            id_front="kyc/id_front/f.jpg",
            id_back="kyc/id_back/b.jpg",
            selfie="kyc/selfie/s.jpg",
            status="pending",
        )

        self.client.force_authenticate(user=self.admin)

        # Admin approves
        resp = self.client.post(
            f"/api/v1/kyc/admin/{sub.id}/review/",
            {"status": "approved"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.verification_status, "verified")
        self.assertTrue(self.user.is_trading_eligible)

    def test_kyc_status_view(self):
        self.client.force_authenticate(user=self.user)

        status_resp = self.client.get("/api/v1/kyc/status/")
        self.assertEqual(status_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(status_resp.data["status"], "unverified")

        KYCSubmission.objects.create(
            user=self.user,
            id_front="kyc/id_front/f.jpg",
            id_back="kyc/id_back/b.jpg",
            selfie="kyc/selfie/s.jpg",
            status="pending",
        )

        status_resp2 = self.client.get("/api/v1/kyc/status/")
        self.assertEqual(status_resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(status_resp2.data["status"], "pending")
