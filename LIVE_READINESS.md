# Readiness checklist — paper-only

This repository is a non-executing scaffold with a read-only market-data audit step. It is not an automated trading bot.

## Implemented
- [x] GitHub Actions syntax/compile/test workflow.
- [x] Fail-closed PAPER-only configuration.
- [x] Read-only CoinDCX futures candle adapter, used only when an exact pair is configured.
- [x] Bounded paper audit reports for missing data, API errors, stale data, insufficient history, duplicate/out-of-order timestamps, and missing/irregular 5-minute intervals.
- [x] Runner blocks before strategy evaluation even when data checks pass.
- [x] Unit tests for risk guardrails, paper-only mode, and paper-runner data quality/fail-closed behavior.
- [x] GitHub Actions secrets are not printed or stored in source.

## Not implemented / not verified
- [ ] Verify exact active CoinDCX INR-margin futures instrument from the official active-instruments list.
- [ ] Actual scheduled-run report/artifact and API connection verified in GitHub Actions.
- [ ] Causal SMC signal logic tested against a clearly specified original strategy and two-month fixtures/logs.
- [ ] Future-data invariance and candle-by-candle vs batch equivalence tests.
- [ ] Persistent paper-position state and restart/replay recovery.
- [ ] Read-only exchange account/position integration, if later required.
- [ ] Mocked paper order lifecycle tests: limit entry, partial fill, rejection, cancellation, duplicate prevention.
- [ ] Paper-only SL/TP lifecycle and exact same-candle SL/TP tie handling.
- [ ] Telegram notification test without logging token/chat secrets.
- [ ] End-to-end paper session with reconciled position state.

## Safety
- Keep `EXECUTION_MODE=PAPER` and `PAPER_ONLY=true`. No live order code is included.
- Do not put credentials in source code, commits, logs, or chat.
- GitHub-hosted scheduled jobs are best-effort and are not a continuous process or a reliable position monitor.
- A passing test suite does not prove profitability or exchange connectivity.
