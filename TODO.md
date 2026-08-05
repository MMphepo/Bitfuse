# TODO — Use airtel.png/tnm.png logos & fix KYC status

## Task 3: Show the logged-in user's actual transaction history

- [x] Diagnose why history page was empty (backend returned no `Transaction` rows for the user)
- [x] Add `OrderHistorySerializer` in `accounts/serializers.py` to map `Order` records into the same shape as `TransactionSerializer` (statuses mapped to frontend `TxStatus`)
- [x] Update `TransactionListView` in `accounts/views.py` to merge the user's `Transaction` + `Order` records, sorted newest-first
- [x] Add debug `console.log` + backend `print` instrumentation
- [x] Fix frontend `getTransactions()` field mapping (snake_case -> camelCase) in `services/api.ts`
- [x] Verify backend `manage.py check` passes
- [x] Verify frontend `tsc --noEmit` passes

## Task 1: Use airtel.png/tnm.png logos & remove descriptions

- [x] Explore repo and understand usage of AirtelLogo/TnmLogo
- [x] Confirm plan with user
- [x] Update `BrandIcons.tsx` to render `airtel.png` and `tnm.png` images (keep size/className props)
- [x] Remove `description` for airtel and tnm payment methods in `buy.tsx`
- [x] Remove `description` for airtel and tnm payout methods in `sell.tsx`
- [x] Make description rendering conditional in buy.tsx and sell.tsx

## Task 2: Fix dashboard KYC status showing "Verify Identity" after load

- [x] Investigate dashboard KYC status rendering
- [x] Identify status enum mismatch (KYC submission uses "approved" vs frontend "verified")
- [x] Normalize `getKycStatus()` in `api.ts` to map "approved" -> "verified"
- [x] Verify TypeScript passes
      </content>
