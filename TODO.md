# Bitfuse — Admin KYC Review Endpoint

## Plan Steps

- [ ] Add `KYCReviewSerializer` in `kyc/serializers.py` (status + rejection_reason)
- [ ] Add `KYCAdminReviewView` (approve/reject) in `kyc/views.py`, admin-only, sync `verification_status`
- [ ] Register `admin/<uuid:submission_id>/review/` route in `kyc/urls.py`
- [ ] Add tests in `kyc/tests.py` (admin approve, non-admin forbidden, 404, status sync)
- [ ] Run `manage.py check` and KYC tests
