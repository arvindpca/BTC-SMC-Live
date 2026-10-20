# Safety audit — paper validation only

This document records the remaining blockers found during a read-only review of the repository. A green CI run verifies the tests that exist; it does not establish exchange execution safety or profitability.

## Fixed in this branch

- The scheduled workflow creates the `state/` directory before cache restore/save. This addresses the observed cache-save warning caused by a missing path.

## Still unresolved — do not treat this as production-ready

1. **Position recovery:** `app/live_runner.py` only calls `manage_open_trade` when local state contains `open_trade`. If an exchange position exists while local state is missing, the bot must reconcile exchange positions and state before considering any new signal.
2. **Cache is not a durable ledger:** GitHub Actions cache is best-effort and not a transactional position database. A successful cache save does not guarantee reliable position recovery or at-most-once order execution.
3. **Order timeout reconciliation:** An API/network error after submitting an order can leave its outcome unknown. Reconcile by exchange order/position identifiers before marking it rejected or allowing a later attempt.
4. **Protective-order confirmation:** Independently verify both exchange-side stop-loss and take-profit after placement and after modifications. Missing fields must mean “unverified”, not “protected”.
5. **Freshness checks:** Reject stale 5-minute and hourly candles before signal evaluation. A completed candle is not necessarily a fresh candle.
6. **Instrument and sizing:** Reconfirm current instrument metadata, quantity step/minimum, margin currency, leverage mode, and the exact semantics of the exchange TP/SL endpoint using read-only or paper tests.
7. **Time-zone policy:** Specify whether the daily trade cap resets at UTC midnight or India time and test the boundary.
8. **Execution tests:** Add deterministic tests for ambiguous timeouts, partial fills, rejected/cancelled limit orders, restart with exchange position but no local state, missing SL/TP fields, and failed stop modifications.
9. **Monitoring:** Telegram delivery and alert failure paths need separate tests. GitHub Actions schedules can be delayed or skipped; this is not a continuous 24/7 process.

## Safe validation sequence

- Keep execution paper-only while addressing the items above.
- Run unit tests with mocked exchange responses; do not send real orders for test coverage.
- Run a reconciled paper session and inspect every signal, simulated fill, SL/TP event, and state transition.
- Do not infer profitability or operational readiness from passing tests alone.
