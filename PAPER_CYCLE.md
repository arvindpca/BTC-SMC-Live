# Offline paper-cycle usage

The paper runner now accepts candle fixtures only; it deliberately does not fetch live market data or submit orders.

Provide `data/5m.json` and `data/1h.json` as JSON arrays. Each item must contain `timestamp`, `open`, `high`, `low`, and `close`; timestamps should be sortable ISO-8601 strings. Supply completed 5-minute and already-closed 1-hour candles only.

Run `python -m app.paper_runner`. The runner writes a report under `reports/`. Possible statuses include `BLOCKED_NO_CANDLE_DATA`, `BLOCKED_INVALID_DATA`, `NO_SIGNAL`, and `SIGNAL_ONLY_LIMIT_NOT_SUBMITTED`.

This is an offline strategy-analysis stage, not a continuous bot. It does not connect to an exchange, does not create real orders, and does not yet integrate the reversal/LIMIT simulator. SMC logic must be validated against the original script and realistic fixtures before any performance claims.
