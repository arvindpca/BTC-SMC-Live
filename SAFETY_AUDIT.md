# Safety audit — paper validation only

This repository is **not approved for live trading**. A green CI run only verifies tests that exist; it does not establish exchange execution safety or profitability.

## Safety lock added

- The GitHub Actions workflow now hard-codes `LIVE_MODE: OFF` and clears `LIVE_CONFIRM`, so repository variables/secrets cannot switch that workflow into live mode.
- This lock applies to this workflow only. It does not disable a manual local invocation of `app.live_runner` if someone supplies live configuration.
- The workflow creates `state/` before cache restore/save, which addresses the observed missing-path warning.

## Unresolved blockers — do not treat this as production-ready

1. **Position recovery:** `app/live_runner.py` only manages an open trade when local state contains `open_trade`. Exchange positions must be reconciled with durable state before any new signal is considered.
2. **Cache is not a durable ledger:** GitHub Actions cache is best-effort and not a transactional position database; it cannot guarantee at-most-once order execution.
3. **Order timeout reconciliation:** The runner now labels submission errors as `ORDER_OUTCOME_UNKNOWN`, keeps the pre-submission duplicate guard, and alerts the operator to inspect CoinDCX manually; it does not yet reconcile using exchange order IDs. Never assume rejection from a timeout.
4. **Protective-order confirmation:** Entry flow now fails safe if SL/TP cannot be read back, if returned prices mismatch, or if the position lookup fails; it also avoids recording a phantom open trade when the position has already closed. The actual CoinDCX response field names/semantics still need read-only verification before relying on these checks.
5. **Freshness checks:** The runner now rejects stale 5-minute/hourly candles when both feeds are present. The production fetcher raises on missing candle data, but empty mocked feeds bypass the freshness comparison; add explicit empty-feed tests before treating this blocker as fully closed.
6. **Instrument and sizing:** Verify current instrument metadata, quantity step/minimum, margin currency, leverage mode, and TP/SL endpoint semantics using read-only/paper tests.
7. **Entry type:** The current runner submits a MARKET order, not the requested LIMIT order.
8. **Time-zone policy:** Define and test whether daily caps reset at UTC midnight or India time.
9. **Execution tests:** Add deterministic tests for ambiguous timeouts, partial fills, rejected/cancelled LIMIT orders, restart with an exchange position but no local state, missing SL/TP fields, and failed stop modifications.
10. **Monitoring and scheduling:** Test Telegram failure paths. GitHub Actions schedules may be delayed or skipped; this is not a continuous 24/7 process.

## Safe next steps

- Keep the hosted workflow hard-locked to OFF.
- Run mocked unit tests and a reconciled paper session only; no real exchange orders for test coverage.
- Do not remove the safety lock until blockers are fixed, reviewed, and paper-tested.
- Do not infer profitability or operational readiness from passing tests alone.
