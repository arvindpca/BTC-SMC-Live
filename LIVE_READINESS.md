# Live readiness checklist — no live execution

This checklist is a gate, not permission to trade. The repository currently does not implement live order placement.

## Configuration
- [ ] Store CoinDCX API key and secret in GitHub Actions Secrets (never in files or chat).
- [ ] Confirm API key permissions are minimum required; avoid withdrawal permissions.
- [ ] Add `COINDCX_FUTURES_INSTRUMENT` as a GitHub Actions repository variable only after confirming the exact active INR-futures instrument in official CoinDCX documentation/account.
- [ ] Add Telegram token and chat ID as Actions Secrets if alerts are desired.

## Technical verification still required
- [ ] Verify exact product type, instrument ID, quote currency and INR margin support.
- [ ] Verify account margin mode, leverage tiers, lot/quantity step and minimum order constraints.
- [ ] Verify official authentication/signature algorithm and request timestamp rules.
- [ ] Implement read-only authenticated account/position query and verify response schemas.
- [ ] Test order-intent construction with mocked exchange responses and duplicate-request protection.
- [ ] Define position reconciliation, restart recovery, idempotency and safe shutdown.
- [ ] Validate exchange-supported protective SL/TP and emergency exit behavior before any future live integration.
- [ ] Review slippage, fees, partial fills, rejected/cancelled limit orders, stale data and rate limits.

## Safety
- Current workflows must not place real orders.
- The preflight prints only whether secrets exist; it never prints secret values.
- Never paste credentials into chat or commit them to Git.
- GitHub-hosted Actions scheduling is best-effort and does not guarantee a continuously running process. Do not rely on it as the sole protection for an open leveraged position.
