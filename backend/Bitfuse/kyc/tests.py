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


class KYCAdminWorkspaceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="admin_workspace",
            email="admin_ws@example.com",
            phone_number="+265999000888",
            password="AdminPassword123!",
        )
        self.regular_user = User.objects.create_user(
            username="user_normal",
            first_name="John",
            last_name="Banda",
            email="john@example.com",
            phone_number="+265999111222",
            national_id_number="NID123456",
            password="UserPassword123!",
            email_verified=True,
            phone_verified=True,
            verification_status="pending",
        )
        self.submission = KYCSubmission.objects.create(
            user=self.regular_user,
            id_front="kyc/id_front/john_f.jpg",
            id_back="kyc/id_back/john_b.jpg",
            selfie="kyc/selfie/john_s.jpg",
            status="pending",
        )

    def test_non_admin_forbidden_from_workspace_endpoints(self):
        self.client.force_authenticate(user=self.regular_user)

        endpoints = [
            ("/api/v1/kyc/admin/stats/", "get"),
            ("/api/v1/kyc/admin/queue/", "get"),
            (f"/api/v1/kyc/admin/{self.submission.id}/", "get"),
            (f"/api/v1/kyc/admin/{self.submission.id}/approve/", "post"),
            (f"/api/v1/kyc/admin/{self.submission.id}/reject/", "post"),
            (f"/api/v1/kyc/admin/{self.submission.id}/request-resubmission/", "post"),
        ]

        for url, method in endpoints:
            handler = getattr(self.client, method)
            resp = handler(url)
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_stats_endpoint(self):
        self.client.force_authenticate(user=self.admin)

        resp = self.client.get("/api/v1/kyc/admin/stats/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["pending"], 1)
        self.assertEqual(resp.data["total"], 1)

    def test_admin_queue_filtering_and_search(self):
        self.client.force_authenticate(user=self.admin)

        # List pending
        resp = self.client.get("/api/v1/kyc/admin/queue/?status=pending")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["user"]["full_name"], "John Banda")

        # Search by national ID
        resp_search = self.client.get("/api/v1/kyc/admin/queue/?search=NID123456")
        self.assertEqual(resp_search.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_search.data["count"], 1)

        # Search non-matching
        resp_nomatch = self.client.get("/api/v1/kyc/admin/queue/?search=NonExistent")
        self.assertEqual(resp_nomatch.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_nomatch.data["count"], 0)

    def test_admin_detail_endpoint(self):
        self.client.force_authenticate(user=self.admin)

        resp = self.client.get(f"/api/v1/kyc/admin/{self.submission.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["user"]["email"], "john@example.com")
        self.assertIn("documents", resp.data["id_front_url"])

    def test_admin_approve_action_creates_audit_log(self):
        self.client.force_authenticate(user=self.admin)

        resp = self.client.post(
            f"/api/v1/kyc/admin/{self.submission.id}/approve/",
            {"note": "Document verified properly"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["submission"]["status"], "approved")

        self.regular_user.refresh_from_db()
        self.assertEqual(self.regular_user.verification_status, "verified")

        # Verify audit log
        self.submission.refresh_from_db()
        logs = self.submission.audit_logs.all()
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs[0].action, "approved")
        self.assertEqual(logs[0].admin, self.admin)

    def test_admin_reject_action_requires_reason(self):
        self.client.force_authenticate(user=self.admin)

        # Without reason -> 400
        resp_fail = self.client.post(
            f"/api/v1/kyc/admin/{self.submission.id}/reject/",
            {"reason": ""},
            format="json",
        )
        self.assertEqual(resp_fail.status_code, status.HTTP_400_BAD_REQUEST)

        # With reason -> 200
        resp_ok = self.client.post(
            f"/api/v1/kyc/admin/{self.submission.id}/reject/",
            {"reason": "ID document is unclear", "note": "Blurry text on front"},
            format="json",
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_ok.data["submission"]["status"], "rejected")

        self.regular_user.refresh_from_db()
        self.assertEqual(self.regular_user.verification_status, "rejected")

    def test_admin_request_resubmission_action(self):
        self.client.force_authenticate(user=self.admin)

        resp = self.client.post(
            f"/api/v1/kyc/admin/{self.submission.id}/request-resubmission/",
            {"reason": "Selfie is dark", "note": "Please re-take selfie with good lighting"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["submission"]["status"], "resubmission_required")

        self.regular_user.refresh_from_db()
        self.assertEqual(self.regular_user.verification_status, "unverified")

        # Resubmit as user
        self.client.force_authenticate(user=self.regular_user)
        id_f = SimpleUploadedFile("f.jpg", b"front", content_type="image/jpeg")
        id_b = SimpleUploadedFile("b.jpg", b"back", content_type="image/jpeg")
        selfie = SimpleUploadedFile("s.jpg", b"selfie", content_type="image/jpeg")

        resub_resp = self.client.post(
            "/api/v1/kyc/submit/",
            {"id_front": id_f, "id_back": id_b, "selfie": selfie},
            format="multipart",
        )
        self.assertEqual(resub_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resub_resp.data["status"], "pending")
