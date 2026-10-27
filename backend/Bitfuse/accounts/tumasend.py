import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class TumaSendError(Exception):
    """Base exception for TumaSend API errors."""
    pass


class TumaSendAuthenticationError(TumaSendError):
    pass


class TumaSendProviderError(TumaSendError):
    pass


class TumaSendTimeoutError(TumaSendError):
    pass


class TumaSendClient:
    """HTTP Client for sending SMS via TumaSend Gateway API."""

    def __init__(self, api_key: str = None, api_url: str = None, sender_id: str = None, timeout: float = 10.0):
        self.api_key = api_key or getattr(settings, "TUMASEND_API_KEY", "")
        self.api_url = (api_url or getattr(settings, "TUMASEND_API_URL", "https://gateway.tumasend.com")).rstrip("/")
        self.sender_id = sender_id or getattr(settings, "TUMASEND_SENDER_ID", "Bitfuse")
        self.timeout = timeout

    def send_sms(self, recipients: list[str], message: str) -> dict:
        """Sends an SMS message to recipient phone numbers.

        Args:
            recipients: List of E.164 phone numbers (e.g. ['+265991234567']).
            message: SMS body text.

        Returns:
            dict containing provider response JSON (e.g. {'batch_id': '...', 'success': True, 'queued': 1}).

        Raises:
            TumaSendAuthenticationError: If API key is missing or rejected (401/403).
            TumaSendTimeoutError: If HTTP request times out.
            TumaSendProviderError: For HTTP 4xx/5xx or malformed provider response.
        """
        if not self.api_key:
            logger.error("[TUMASEND_ERROR] TUMASEND_API_KEY is missing or empty.")
            raise TumaSendAuthenticationError("TumaSend API key is not configured.")

        endpoint = f"{self.api_url}/api/v1/send/sms"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }
        payload = {
            "from": self.sender_id,
            "recipients": recipients,
            "message": message,
        }

        # Mask recipients in logs
        masked_recipients = [f"{r[:6]}***{r[-3:]}" if len(r) > 9 else "***" for r in recipients]
        logger.info(f"[TUMASEND_REQUEST] Sending SMS via TumaSend to {masked_recipients} (Sender: {self.sender_id})")

        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=self.timeout)
        except requests.Timeout as exc:
            logger.error(f"[TUMASEND_TIMEOUT] TumaSend request timed out: {exc}")
            raise TumaSendTimeoutError("TumaSend SMS request timed out.") from exc
        except requests.RequestException as exc:
            logger.error(f"[TUMASEND_NETWORK_ERROR] Failed to connect to TumaSend: {exc}")
            raise TumaSendProviderError("Network failure connecting to TumaSend SMS gateway.") from exc

        if response.status_code in (401, 403):
            logger.error(f"[TUMASEND_AUTH_ERROR] TumaSend authentication failed with HTTP {response.status_code}")
            raise TumaSendAuthenticationError("Invalid or unauthorized TumaSend API credentials.")

        try:
            response.raise_for_status()
            res_json = response.json()
        except requests.HTTPError as exc:
            logger.error(f"[TUMASEND_HTTP_ERROR] TumaSend returned status {response.status_code}: {response.text}")
            raise TumaSendProviderError(f"TumaSend returned HTTP status {response.status_code}.") from exc
        except ValueError as exc:
            logger.error(f"[TUMASEND_JSON_ERROR] Failed to parse TumaSend JSON response: {response.text}")
            raise TumaSendProviderError("Invalid JSON response received from TumaSend.") from exc

        # Validate business success flag in payload
        if not res_json.get("success"):
            logger.warning(f"[TUMASEND_FAILED_RESPONSE] TumaSend response success=False: {res_json}")
            raise TumaSendProviderError("TumaSend reported SMS queueing failure.")

        logger.info(f"[TUMASEND_SUCCESS] SMS queued successfully. Batch ID: {res_json.get('batch_id')}")
        return res_json
