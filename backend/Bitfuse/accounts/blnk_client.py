import requests
from django.conf import settings


class BlnkClient:
    def __init__(self):
        self.base_url = settings.BLNK_BASE_URL
        self.headers = {"Content-Type": "application/json"}
        if settings.BLNK_SECRET_KEY:
            self.headers["X-Blnk-Key"] = settings.BLNK_SECRET_KEY

    def create_ledger(self, name: str, meta: dict | None = None):
        print(f"[DEBUG] create_ledger called with name={name!r}, meta={meta!r}")
        resp = requests.post(
            f"{self.base_url}/ledgers",
            json={"name": name, "meta_data": meta or {}},
            headers=self.headers,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"[DEBUG] create_ledger returned: {data}")
        return data

    def create_balance(self, ledger_id: str, currency: str, meta: dict | None = None):
        print(f"[DEBUG] create_balance called with ledger_id={ledger_id!r}, currency={currency!r}, meta={meta!r}")
        resp = requests.post(
            f"{self.base_url}/balances",
            json={"ledger_id": ledger_id, "currency": currency, "meta_data": meta or {}},
            headers=self.headers,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"[DEBUG] create_balance returned: {data}")
        return data

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
        print(
            f"[DEBUG] create_transaction called with amount={amount!r}, currency={currency!r}, "
            f"precision={precision!r}, reference={reference!r}, source={source!r}, "
            f"destination={destination!r}, description={description!r}"
        )
        resp = requests.post(
            f"{self.base_url}/transactions",
            json={
                "amount": amount,
                "currency": currency,
                "precision": precision,
                "reference": reference,
                "source": source,
                "destination": destination,
                "description": description,
            },
            headers=self.headers,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"[DEBUG] create_transaction returned: {data}")
        return data

    def get_balance(self, balance_id: str):
        """Fetch a balance's current numeric value from Blnk.

        Returns dict with keys: balance_id, balance, credit_balance, debit_balance, currency.
        """
        print(f"[DEBUG] get_balance called with balance_id={balance_id!r}")
        resp = requests.get(
            f"{self.base_url}/balances/{balance_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"[DEBUG] get_balance returned: {data}")
        return data
