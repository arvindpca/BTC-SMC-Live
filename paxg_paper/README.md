# PAXG S1 paper trader (EDUCATIONAL PURPOSE ONLY)

**Education only.** This is a *paper* trading experiment: no API keys, no orders, no real money.
It is not financial advice, and backtest/paper results do not guarantee any future result.
Leveraged futures can lose all capital.

## What it does
Every 5 minutes (GitHub Actions, `.github/workflows/paxg-paper.yml`) it reads public CoinDCX `B-PAXG_USDT` 5m candles and
paper-trades one rule set:

1. Asia range = high/low of 00:00-07:00 UTC (Mon-Fri, max 1 trade/day).
2. Trend: previous completed daily close vs daily EMA20 (above -> long only, below -> short only).
3. Signal: first 15m close beyond the range in the trend direction between 07:00-12:00 UTC.
4. Direction confirmation (v2): the last completed 4h close must agree with the side (4h close vs 4h EMA20), and the signal
   candle must close >= 0.5 x ATR(14) beyond the range edge; otherwise skip the day.
5. Entry: open of the next candle (paper limit fill). Stop: opposite side of the range (skip if distance <0.3% or >3%).
   Target: 1.5x stop distance. Time exit: 16:00 UTC.

Paper costs: maker 0.025% + 0.03% slippage per side. Defaults: paper capital Rs 3,000 x 10x leverage, USD/INR 88
(change via repo *variables* `PAXG_PAPER_CAPITAL_INR`, `PAXG_PAPER_LEVERAGE`, `PAXG_PAPER_USDINR`).

## Where to see results
* Branch **`paxg-paper-state`**: `STATUS.md` (open position, closed-trade statement, win rate, last 20 trades),
  `trades.csv` (full journal), `state.json`.
* Optional Telegram messages (entry, exit with gross/net P&L and win rate, daily summary): add repo secrets
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Caveats
Backtest sample was small (~51 trades for the v2 filters on 5m data, one asset, edge concentrated in 2026); the same rules showed no edge on BTC.
GitHub schedules can be delayed or skipped, paper fills assume your limit order fills at the next candle open, and
funding/spread are not modelled. Run it for 40-50 trades before drawing conclusions.

Validate the logic offline: `python -m paxg_paper.paper replay candles.csv` (columns time[ms],open,high,low,close,volume).
