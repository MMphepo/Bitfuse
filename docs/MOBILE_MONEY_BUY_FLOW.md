# Mobile money buy flow (Airtel Money / TNM Mpamba)

Bitfuse has no direct Airtel/TNM API yet, so a buy is a manual cash-in rail with
automated order tracking and controlled admin settlement:

```
create buy order → rate locked → user pays externally → user submits transaction ID
→ admin verifies against the mobile money statement → settlement engine → Blnk → USDT credited
```

The admin only ever confirms *that the payment is real*. The USDT amount comes from
the order, and the ledger movement is performed by the settlement service — never by
the frontend and never by hand.

## Order states

```
awaiting_payment → payment_submitted → under_review → payment_verified → settling → completed
        ↓                  ↓                  ↓
     expired           rejected        payment_mismatch
```

`payment_mismatch` is payable again: the user can submit a corrected transaction ID.

## Endpoints

| Method | Path | Who | Purpose |
| ------ | ---- | --- | ------- |
| POST | `/api/v1/orders/buy/` | user (KYC verified) | Create the order, lock the rate, start the payment window |
| GET | `/api/v1/orders/{id}/` | owner | Payment screen: total payable, merchant code, reference, seconds remaining |
| POST | `/api/v1/orders/{id}/payment/` | owner | Submit the mobile money transaction ID |
| GET | `/api/v1/orders/verification-queue/` | verifier | Payment Verification Center queue |
| GET/POST | `/api/v1/orders/{id}/review/` | verifier | Full review sheet / claim the order for review |
| POST | `/api/v1/orders/{id}/verify-payment/` | verifier | Approve (`{"confirm": true}`) and settle |
| POST | `/api/v1/orders/{id}/reject-payment/` | verifier | Reject with a reason |
| GET | `/api/v1/notifications/` | user | Order lifecycle notifications |

Approvals require `confirm: true`. Passing `received_amount` that differs from the
expected total parks the order in `payment_mismatch` instead of crediting.

## Guarantees

- **Rate lock** — `expires_at = created_at + PAYMENT_WINDOW_MINUTES`; expired orders
  cannot be paid (`manage.py expire_orders` sweeps stale ones).
- **One transaction ID, one order** — enforced by a partial unique constraint.
- **Idempotent settlement** — `OrderSettlement` is a one-to-one row created before the
  Blnk calls inside the same transaction, so a double approval cannot double-credit.
- **Audit trail** — every transition is written to `OrderAuditLog` with actor,
  from/to status and note; settlement records who approved it.
- **Least privilege** — settlement endpoints require staff in the `Payment Verifiers`
  group (or a superuser); admins cannot change the USDT amount when approving.

## Configuration

| Setting | Default | Meaning |
| ------- | ------- | ------- |
| `PAYMENT_WINDOW_MINUTES` | 15 | How long a locked rate is honoured |
| `AIRTEL_MONEY_BUSINESS_CODE` | "" | Merchant code shown to buyers |
| `AIRTEL_MONEY_ACCOUNT_NAME` | Bitfuse | Merchant name shown to buyers |
| `TNM_MPAMBA_BUSINESS_CODE` | "" | Merchant code shown to buyers |
| `TNM_MPAMBA_ACCOUNT_NAME` | Bitfuse | Merchant name shown to buyers |
