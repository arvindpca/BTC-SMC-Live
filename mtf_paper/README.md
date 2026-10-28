# MTF Paper Trader (v3): 1D trend > 1H zone > 15M entry, BTC + PAXG

**EDUCATIONAL PURPOSE ONLY.** Paper trading only: no API keys, no orders, no real money. Not financial advice.
Backtest/paper results do not guarantee anything. Results are shown **gross** (no fees); fees and slippage reduce results.

## Rules
1. Daily trend: previous completed daily close vs EMA20 -> only that side.
2. 1H zone: pivot low/high (2 bars each side), width 0.5 x ATR14(1H), valid 5 days.
3. 15m trigger: price touches the zone, then within 4h a candle closes beyond the previous 3 candles' high (BUY) / low (SELL). Entry = next candle open.
4. Stop = far edge of the zone, target = 2R, time exit 12h. After an entry, no new trade on that symbol for 12h.
5. Filters (thresholds learned on the first 60% of history): stop distance >= BTC 0.46% / PAXG 0.23%, zone age <= 48h, daily trend strength >= BTC 1.15% / PAXG 0.57%.

Paper account: Rs.3,000 per symbol x 30x = Rs.90,000 per trade (BTC and PAXG tracked separately and combined).

## Where to look
- Live tracking: branch `mtf-paper-state` -> `STATUS.md` (open positions, statement, month-wise P/L, last trades)
- Journal: `trades.csv` on the same branch
- Runner: Actions -> "MTF Paper Trader 1D>1H>15M BTC+PAXG" (auto-restarts itself; cron is a safety net)
- Messages (tagged `[v3 1D>1H>15M]`): start, entry, exit + statement, daily summary at 12:00 AM IST. Optional Telegram via secrets
  `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`; otherwise read the run log.

## Limits
Signals are checked once per closed 15m candle (cycle at :02/:17/:32/:47 UTC), so exits are seen up to ~15 min late and the paper fill is at the candle open. Backtest history was Binance data; live uses CoinDCX candles.
