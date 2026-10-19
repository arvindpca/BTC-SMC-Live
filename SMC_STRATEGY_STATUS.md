# SMC strategy implementation status

## Current status: NOT VERIFIED — DO NOT TREAT AS LIVE-READY

The current `app/strategy_smc.py` is a first implementation, not a verified reproduction of the user's two-month paper-trading strategy.

## Audit findings from the current source

- `_swings()` evaluates swing candidates using candles on both sides of each candidate. That means a swing is only knowable after later candles close; code must not treat it as known at the candidate candle's timestamp.
- `evaluate_signal()` scans future 5-minute candles for a retest and can return a signal timestamped at that later retest. This is not a signal that was available at the start of the evaluation cycle.
- Higher-timeframe bias depends on the exact candle timestamp/close convention. Comparing only ISO timestamp strings is not enough to prove a 1H candle was fully closed before a 5M decision.
- Existing strategy tests do not test future-data invariance, online-vs-batch equivalence, completed-candle timing, or a known expected signal from the original strategy.
- The read-only CoinDCX adapter is now called by the bounded paper audit runner when an exact futures pair is configured. The runner checks minimum history, timestamp ordering/duplicates, candle freshness, and 5-minute interval continuity.
- The runner deliberately stops after data-quality checks. It does not call `evaluate_signal()`, maintain positions, or submit orders. This is intentional until the strategy's causal behavior and equivalence are proven.

## Required correctness criteria before any further status change

1. Preserve the original strategy specification and compare each rule against the original source and two-month paper-trading logs/fixtures.
2. A signal emitted at time T must depend only on candles fully closed and available by T.
3. Appending future candles must never change a signal that was already emitted for an earlier timestamp.
4. Batch processing and candle-by-candle processing must agree on each historical decision timestamp.
5. Test H1 close-time alignment, duplicate/missing/out-of-order candles, restart/replay behavior, and exact SL/TP tie handling.
6. Keep the runner fail-closed until the above tests and data checks are complete.

## Safety/status

- This repository remains PAPER-only and has no exchange order-submission implementation.
- Passing syntax/unit tests does not prove strategy equivalence, profitability, exchange connectivity, or live readiness.
