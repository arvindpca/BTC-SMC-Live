# PAXG S1 "London breakout of Asia range" - final rules (research only, not advice)

Instrument: CoinDCX B-PAXG_USDT futures, 15m. Mon-Fri, max 1 trade/day. Times UTC (IST = UTC+5:30).

1. Asia range = high/low of 00:00-07:00 UTC.
2. Trend: previous day's daily close vs its daily EMA20. Above -> long only, below -> short only.
3. Signal: first 15m candle CLOSE beyond the range in trend direction, between 07:00 and 12:00 UTC.
4. Volume filter (added after loss analysis): signal candle volume <= 1.5 x its 20-candle average volume. Otherwise skip the day.
5. Entry: next candle open (use a LIMIT/maker order).
6. Stop: opposite side of Asia range. Skip if stop distance < 0.3% or > 3% of price.
7. Target: 1.5 x stop distance.
8. Time exit: 16:00 UTC if neither hit.
9. Size: risk <= 1% of account per trade (size = account*1% / stop distance). Do NOT size by leverage.

Evidence (maker 0.025% + 0.03% slippage per side, % returns, train = first 60%, test = last 40%):
- PAXG base:      train PF 0.89 (n=75) | test PF 2.99 (n=50)
- PAXG vol<=1.5:  train PF 1.62 (n=31) | test PF 5.70 (n=20)
- BTC (same rules, independent check): no edge, PF 0.79-0.99 -> strategy is PAXG-specific / regime-dependent.
Caveats: small samples, filter chosen from 5 candidates, edge concentrated in 2026, funding/spread not modelled.
Reproduce: python filter_walkforward.py
