# Reversal + LIMIT — paper-only specification

This standalone helper does not connect to CoinDCX or submit real orders.

1. Wait for the existing strategy's confirmed signal at a completed 5-minute candle close.
2. If an opposite paper position exists, record it as closed at that signal close.
3. Queue one opposite-side LIMIT order priced at the signal candle close.
4. The new position is not open while the order is pending.
5. A later candle fills the simulated order only if its low/high range contains the limit price. This is a simplified paper-fill assumption, not a guarantee of execution.
6. Same-side signals are ignored to prevent duplicate positions.
7. Pending-order expiry is a caller-selected max_wait_bars parameter; no duration is silently assumed.
8. Stop/target prices are supplied by the caller and validated against the LIMIT price.

Important: this helper is not wired into paper_runner.py or the SMC strategy. Market-data and strategy integration remain incomplete, so unit tests do not mean the full paper bot is running.
