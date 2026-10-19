# BTC-SMC-Live

**Status: validation + paper-trading scaffold only. Live order placement is not implemented or enabled.**

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
