# TODO — Fix Buy Order "can't multiply sequence by non-int" TypeError

## Root Cause

`price_buy_order()` received `usdt_amount` as a string when multiplied by the
Decimal `rate.buy_rate`, causing `TypeError: can't multiply sequence by non-int
of type 'decimal.Decimal'`. The deployed backend is also stale (older than local).

## Steps

EFACTOR

- [x] 1. Analyze error and trace the data flow (services / serializers / frontend).
- [x] 2. Add `_to_decimal()` helper in `backend/Bitfuse/orders/services.py`.
- [x] 3. Use `_to_decimal()` in `price_buy_order()` and `price_sell_order()`.
- [x] 4. Coerce `amount_usdt` to Decimal in `orders/serializers.py` before pricing.
- [x] 5. Python syntax compile passed for all edited files (ALL SYNTAX OK).
     `manage.py check` blocked locally by missing `dj_database_url` (env issue, unrelated to change).
- [ ] 6. Commit and redeploy backend to Render (deployed image is stale).
