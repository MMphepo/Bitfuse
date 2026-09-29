import logging
import random
import time
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class BlnkClient:
    """Robust, central client for Blnk Ledger with HTTP 429 rate limit backoff and retries."""

    def __init__(self, max_retries: int = 1, backoff_factor: float = 0.5, timeout: float = 3.0):
        self.base_url = settings.BLNK_BASE_URL.rstrip('/') if settings.BLNK_BASE_URL else ""
        self.headers = {"Content-Type": "application/json"}
        api_key = getattr(settings, "BLNK_API_KEY", "")
        if api_key:
            self.headers["X-Blnk-Key"] = api_key
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.default_timeout = timeout

    def _request(
        self,
        method: str,
        endpoint: str,
        max_retries: int | None = None,
        timeout: float | None = None,
        **kwargs,
    ) -> dict:
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("headers", self.headers)
        req_timeout = timeout if timeout is not None else self.default_timeout
        kwargs.setdefault("timeout", req_timeout)
        retries = max_retries if max_retries is not None else self.max_retries

        start_time = time.monotonic()

        for attempt in range(retries + 1):
            attempt_start = time.monotonic()
            try:
                logger.debug(f"[BLNK_REQUEST] {method} {endpoint} (attempt {attempt + 1}/{retries + 1})")
                resp = requests.request(method, url, **kwargs)
                duration = time.monotonic() - attempt_start

                # Handle HTTP 429 Too Many Requests
                if resp.status_code == 429:
                    logger.warning(
                        f"[BLNK_429] {method} {endpoint} status=429 duration={duration:.3f}s attempt={attempt + 1}"
                    )
                    if attempt < retries:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            sleep_time = min(float(retry_after), 1.0)
                        else:
                            sleep_time = min(self.backoff_factor * (2 ** attempt) + random.uniform(0.05, 0.2), 1.0)

                        time.sleep(sleep_time)
                        continue
                    else:
                        resp.raise_for_status()

                # Handle transient server errors (500, 502, 503, 504)
                if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                    logger.warning(
                        f"[BLNK_5XX] {method} {endpoint} status={resp.status_code} duration={duration:.3f}s attempt={attempt + 1}"
                    )
                    sleep_time = min(self.backoff_factor * (2 ** attempt) + random.uniform(0.05, 0.2), 1.0)
                    time.sleep(sleep_time)
                    continue

                resp.raise_for_status()
                total_duration = time.monotonic() - start_time
                logger.debug(
                    f"[BLNK_SUCCESS] {method} {endpoint} status={resp.status_code} duration={total_duration:.3f}s"
                )
                return resp.json() if resp.content else {}

            except (requests.Timeout, requests.ConnectionError) as exc:
                duration = time.monotonic() - attempt_start
                if attempt < retries:
                    logger.warning(
                        f"[BLNK_TIMEOUT] {method} {endpoint} error={exc} duration={duration:.3f}s attempt={attempt + 1}"
                    )
                    sleep_time = min(self.backoff_factor * (2 ** attempt) + random.uniform(0.05, 0.2), 1.0)
                    time.sleep(sleep_time)
                    continue
                logger.error(f"[BLNK_CONNECTION_ERROR] {method} {endpoint} failed after {attempt + 1} attempts: {exc}")
                raise exc
            except requests.HTTPError as exc:
                duration = time.monotonic() - attempt_start
                logger.error(
                    f"[BLNK_HTTP_ERROR] {method} {endpoint} status={exc.response.status_code if exc.response is not None else 'unknown'} duration={duration:.3f}s"
                )
                raise exc
            except requests.RequestException as exc:
                duration = time.monotonic() - attempt_start
                if attempt < retries:
                    logger.warning(f"[BLNK_CONNECTION_ERROR] {method} {endpoint}: {exc}. Retrying...")
                    sleep_time = min(self.backoff_factor * (2 ** attempt) + random.uniform(0.05, 0.2), 1.0)
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

    def get_ledger(self, ledger_id: str):
        """Fetch ledger details from Blnk."""
        logger.debug(f"[BLNK] Fetching ledger: ledger_id={ledger_id!r}")
        return self._request("GET", f"/ledgers/{ledger_id}")

    def ledger_exists(self, ledger_id: str) -> bool:
        """Check if a ledger exists in Blnk.

        Returns True if the ledger exists.
        Returns False ONLY if Blnk explicitly responds with 404 Not Found.
        Raises HTTPError/Timeout/ConnectionError for any service outage or non-404 error.
        """
        if not ledger_id or not str(ledger_id).strip():
            return False
        try:
            self.get_ledger(ledger_id)
            logger.debug(f"[BLNK_LEDGER_VALIDATE] Ledger {ledger_id} exists in Blnk.")
            return True
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.warning(f"[BLNK_LEDGER_STALE] Ledger {ledger_id} returned 404 Not Found in Blnk.")
                return False
            raise exc

    def balance_exists(self, balance_id: str) -> bool:
        """Check if a balance exists in Blnk.

        Returns True if the balance exists.
        Returns False ONLY if Blnk explicitly responds with 404 Not Found.
        Raises HTTPError/Timeout/ConnectionError for any service outage or non-404 error.
        """
        if not balance_id or not str(balance_id).strip():
            return False
        try:
            self.get_balance(balance_id)
            logger.debug(f"[BLNK_BALANCE_VALIDATE] Balance {balance_id} exists in Blnk.")
            return True
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.warning(f"[BLNK_BALANCE_STALE] Balance {balance_id} returned 404 Not Found in Blnk.")
                return False
            raise exc

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
