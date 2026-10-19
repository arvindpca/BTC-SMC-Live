# Readiness checklist — paper-only

This repository is a non-executing scaffold. It is not an automated trading bot.

## Implemented
- [x] GitHub Actions syntax/compile/test workflow.
- [x] Fail-closed PAPER-only configuration.
- [x] One bounded paper runner that emits an explicit blocked report while strategy/data adapters are missing.
- [x] Unit tests for risk guardrails and paper-only mode.
- [x] GitHub Actions secrets are not printed or stored in source.

## Not implemented / not verified
- [ ] Live market-data adapter and timestamp/candle validation.
- [ ] SMC signal logic and tests against a clearly specified strategy.
- [ ] Persistent position state and restart recovery.
- [ ] Exchange account/position read-only integration.
- [ ] Contract identity, quantity step, minimum order and margin requirements verified.
- [ ] Mocked order lifecycle tests: limit entry, partial fill, rejection, cancellation, duplicate prevention.
- [ ] Exchange-side SL/TP verification and safe failure handling.
- [ ] Telegram notification test without logging token/chat secrets.
- [ ] End-to-end paper session with reconciled position state.

## Safety
- Keep EXECUTION_MODE=PAPER. No live order code is included.
- Do not put credentials in source code, commits, logs, or chat.
- GitHub-hosted scheduled jobs are best-effort and are not a continuous process or a reliable position monitor.
- A passing test suite does not prove profitability or exchange connectivity.
