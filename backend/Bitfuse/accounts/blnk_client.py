import logging
import time
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class BlnkClient:
    """Robust, central client for Blnk Ledger with HTTP 429 rate limit backoff and retries."""

    def __init__(self, max_retries: int = 5, backoff_factor: float = 1.0):
        self.base_url = settings.BLNK_BASE_URL.rstrip('/') if settings.BLNK_BASE_URL else ""
        self.headers = {"Content-Type": "application/json"}
        if getattr(settings, "BLNK_SECRET_KEY", None):
            self.headers["X-Blnk-Key"] = settings.BLNK_SECRET_KEY
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("timeout", 10)

        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.request(method, url, **kwargs)

                # Handle HTTP 429 Too Many Requests
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        sleep_time = float(retry_after)
                    else:
                        sleep_time = self.backoff_factor * (2 ** attempt)

                    if attempt < self.max_retries:
                        logger.warning(
                            f"[BLNK] Received HTTP 429 for {method} {endpoint}. "
                            f"Retrying attempt {attempt + 1}/{self.max_retries} after {sleep_time:.2f}s..."
                        )
                        time.sleep(sleep_time)
                        continue
                    else:
                        logger.error(f"[BLNK] Exhausted retries after HTTP 429 for {method} {endpoint}")
                        resp.raise_for_status()

                # Handle transient 5xx server errors
                if 500 <= resp.status_code < 600 and attempt < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** attempt)
                    logger.warning(
                        f"[BLNK] Received HTTP {resp.status_code} for {method} {endpoint}. "
                        f"Retrying attempt {attempt + 1}/{self.max_retries} after {sleep_time:.2f}s..."
                    )
                    time.sleep(sleep_time)
                    continue

                resp.raise_for_status()
                return resp.json() if resp.content else {}

            except requests.RequestException as exc:
                if attempt < self.max_retries and not isinstance(exc, requests.HTTPError):
                    sleep_time = self.backoff_factor * (2 ** attempt)
                    logger.warning(f"[BLNK] Connection error for {method} {endpoint}: {exc}. Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    continue
                raise exc

        raise RuntimeError(f"[BLNK] Request {method} {endpoint} failed after max retries.")

    def create_ledger(self, name: str, meta: dict | None = None):
        logger.debug(f"[BLNK] Creating ledger: name={name!r}")
        return self._request("POST", "/ledgers", json={"name": name, "meta_data": meta or {}})

    def create_balance(self, ledger_id: str, currency: str, meta: dict | None = None):
        logger.debug(f"[BLNK] Creating balance: ledger_id={ledger_id!r}, currency={currency!r}")
        return self._request(
            "POST",
            "/balances",
            json={"ledger_id": ledger_id, "currency": currency, "meta_data": meta or {}},
        )

    def create_transaction(
        self,
        amount: int,
        currency: str,
        precision: int,
        reference: str,
        source: str,
        destination: str,
        description: str = "",
    ):
        logger.debug(
            f"[BLNK] Creating transaction: reference={reference!r}, amount={amount!r}, "
            f"source={source!r}, destination={destination!r}"
        )
        return self._request(
            "POST",
            "/transactions",
            json={
                "amount": amount,
                "currency": currency,
                "precision": precision,
                "reference": reference,
                "source": source,
                "destination": destination,
                "description": description,
            },
        )

    def get_balance(self, balance_id: str):
        """Fetch a balance's current numeric value from Blnk."""
        logger.debug(f"[BLNK] Fetching balance: balance_id={balance_id!r}")
        return self._request("GET", f"/balances/{balance_id}")

    def get_transaction(self, transaction_id: str):
        """Fetch a transaction's current status and details from Blnk."""
        logger.debug(f"[BLNK] Fetching transaction: transaction_id={transaction_id!r}")
        return self._request("GET", f"/transactions/{transaction_id}")

    def list_ledgers(self):
        """Fetch all ledgers from Blnk."""
        logger.debug("[BLNK] Listing ledgers")
        return self._request("GET", "/ledgers")

    def list_balances(self):
        """Fetch all balances from Blnk."""
        logger.debug("[BLNK] Listing balances")
        return self._request("GET", "/balances")
