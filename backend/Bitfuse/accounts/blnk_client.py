import requests
from django.conf import settings


class BlnkClient:
    def __init__(self):
        self.base_url = settings.BLNK_BASE_URL
        self.headers = {"Content-Type": "application/json"}
        if settings.BLNK_SECRET_KEY:
            self.headers["X-Blnk-Key"] = settings.BLNK_SECRET_KEY

    def create_ledger(self, name: str, meta: dict | None = None):
        resp = requests.post(
            f"{self.base_url}/ledgers",
            json={"name": name, "meta_data": meta or {}},
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()

    def create_balance(self, ledger_id: str, currency: str, meta: dict | None = None):
        resp = requests.post(
            f"{self.base_url}/balances",
            json={"ledger_id": ledger_id, "currency": currency, "meta_data": meta or {}},
            headers=self.headers,
        )
        resp.raise_for_status()
        return resp.json()
