# BTC-SMC-Live

**Status: paper scaffold + gated LIVE executor (OFF by default). No order can be sent until you set `LIVE_MODE` yourself.**

Private repository for an isolated CoinDCX INR-futures SMC research workflow. This project is deliberately separate from any existing bot.

## GitHub Actions architecture

- `.github/workflows/validate.yml`: syntax and unit/safety tests on pushes, pull requests, and manual dispatch.
- `.github/workflows/paper-trading.yml`: one bounded, paper-only cycle on manual dispatch or a scheduled attempt every 15 minutes.
- Every hosted runner is temporary. Scheduled workflows can be delayed, skipped, or unavailable; this is **not** a 24/7 execution guarantee.
- The runner filesystem is ephemeral. Paper position state is currently an artifact/report, not a durable database; don't treat it as a production position ledger.

## Start here

1. Open **Actions** and enable workflows if GitHub asks.
2. Run **BTC-SMC Validation** first.
3. Run **BTC-SMC Paper Trading** manually to check the scaffold.
4. Inspect the generated artifact and logs before connecting any credentials.

## Secrets

Do not commit `.env`, CoinDCX API keys/secrets, Telegram bot tokens, or chat IDs. Add required credentials later in **Settings → Secrets and variables → Actions** only after their usage is implemented and reviewed. Current scaffold does not need trading credentials and does not submit exchange orders.

## Strategy / instrument readiness

This scaffold does not yet claim a verified BTC INR-futures symbol, market-data feed, SMC strategy implementation, or exchange-specific contract sizing. Those require instrument/API verification and separate tests before any live-mode work. No profitability claim is made.

## Risk note

Leveraged futures can lose money quickly. Paper results do not guarantee live execution or returns. Python-only exits may fail when the worker is unavailable; any future live design needs exchange-side protective-order validation and explicit fail-safe handling.

## Live trading (CoinDCX INR futures) - staged, OFF by default

Workflow: `.github/workflows/live-trading.yml` (every 5 min, best-effort). Code: `app/live_runner.py`, `app/strategy_live.py`, `app/coindcx_futures.py`.

Set in **Settings -> Secrets and variables -> Actions**:

| Type | Name | Value |
|---|---|---|
| Secret | `COINDCX_API_KEY`, `COINDCX_API_SECRET` | trade-only key, **withdrawals disabled** |
| Secret | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional alerts |
| (fixed) | pair | locked to `B-BTC_USDT` in code and workflow; any other pair is refused. Margin is still INR. |
| Variable | `MARGIN_INR` / `LEVERAGE` | default `300` / `30` (hard caps: 500 / 30) |
| Variable | `LIVE_MODE` | `OFF` (default) -> `DRY_RUN` -> `LIVE` |
| Variable | `LIVE_CONFIRM` | must equal `YES_TRADE_REAL_MONEY` for `LIVE` |

Stages: 1) `DRY_RUN` for a few days: real data + real signals + read-only exchange calls, logs the exact order it would send. 2) `LIVE` only after the dry-run Telegram/log output looks right; first live trade should be the smallest size the exchange allows. Set `LIVE_MODE=OFF` to stop new entries (open positions keep their exchange-side SL/TP).

Risk filter: the paper bot's 0.05%-2.5% risk filter is removed. Only a technical guard remains: a signal whose stop is wider than about 90% of the liquidation distance (3% at 30x) is skipped, because that stop could never trigger.

Behaviour: market entry on the closed 5m rejection candle, exchange-side stop-loss at the sweep extreme and take-profit at 4R. If SL/TP cannot be placed or verified, or the fill is far from plan, the position is closed immediately. One position at a time (checked on the exchange), max 3 trades/day, each signal is traded at most once. The paper bot's trailing is ported: when completed 5m candles after entry reach 2R the exchange SL is moved to 2R, at 3R to 3R, and 4R is the exchange take-profit (one position, no partial exits). Because the job runs every ~5 minutes, a stop move can be up to one candle late; if the SL move fails the original SL stays active and you get an alert. Moving an existing SL through CoinDCX's TP/SL endpoint is unverified - watch the first live trade.

Unverified against live CoinDCX (check during DRY_RUN / first tiny LIVE trade): the INR-margin fields (`margin_currency_short_name`, `position_margin_type`) and the `stop_loss_trigger` field used to verify protection. Minimum order size can exceed Rs200 x 30 on BTC; the bot then blocks the trade and tells you the margin it would need.
