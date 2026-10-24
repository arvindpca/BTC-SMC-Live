"""10-day backtest of the live BTC SMC strategy on real CoinDCX B-BTC_USDT candles (read-only, no orders).

Trade rules mirror the live bot: entry at the signal bar close, initial SL at the sweep extreme, 4R target,
SL trails to 2R after price reaches 2R and to 3R after 3R (applied from the NEXT candle on).  Inside one
candle the stop is checked before the target (pessimistic).  Fees are an explicit assumption (FEE_PCT per side).
"""
from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.market_data import fetch_latest_candles
from app.strategy_live import scan_signals

PAIR = "B-BTC_USDT"
DAYS = int(os.getenv("BACKTEST_DAYS", "10"))
WARMUP_DAYS = 3
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
NOTIONAL_INR = float(os.getenv("MARGIN_INR", "300")) * float(os.getenv("LEVERAGE", "30"))
FEE_PCT = float(os.getenv("BACKTEST_FEE_PCT", "0.06"))  # assumed taker fee per side, % of notional
IST = timezone(timedelta(hours=5, minutes=30))


def fetch_range(resolution, seconds_per, total_seconds, end):
    out, cursor = {}, end
    need = total_seconds // seconds_per + 5
    while len(out) < need:
        chunk = fetch_latest_candles(PAIR, 500, now_seconds=cursor, resolution=resolution)
        if not chunk:
            break
        before = len(out)
        for c in chunk:
            out[int(c["timestamp"])] = c
        if len(out) == before:
            break
        cursor = int(min(out)) // 1000 - 1
        time.sleep(0.15)
    return [out[k] for k in sorted(out)]


def simulate(sig, c5):
    entry, stop, target = sig.entry, sig.stop, sig.target
    long_ = sig.side == "LONG"
    d = 1 if long_ else -1
    risk = abs(entry - stop)
    cur_stop, max_rr = stop, 0
    for c in c5[sig.bar_index + 1:]:
        hi, lo = float(c["high"]), float(c["low"])
        if (lo <= cur_stop) if long_ else (hi >= cur_stop):
            r = d * (cur_stop - entry) / risk
            return r, c["timestamp"], {0: "SL", 2: "TRAIL_2R", 3: "TRAIL_3R"}[max_rr]
        if (hi >= target) if long_ else (lo <= target):
            return 4.0, c["timestamp"], "TARGET_4R"
        best = hi if long_ else lo
        for rr in (3, 2):
            if rr > max_rr and ((best >= entry + d * rr * risk) if long_ else (best <= entry + d * rr * risk)):
                max_rr, cur_stop = rr, entry + d * rr * risk
                break
    last = c5[-1]
    return d * (float(last["close"]) - entry) / risk, last["timestamp"], "OPEN_AT_END"


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%d-%b %H:%M")


def main():
    end = int(time.time())
    c5 = fetch_range("5", 300, (DAYS + WARMUP_DAYS) * 86400, end)
    c1 = fetch_range("60", 3600, (DAYS + WARMUP_DAYS + 5) * 86400, end)
    start_ms = (end - DAYS * 86400) * 1000
    signals = [s for s in scan_signals(c5, c1) if s.bar_time >= start_ms]
    rows, equity = [], 0.0
    busy_until, per_day = 0, {}
    for s in signals:
        day = datetime.fromtimestamp(s.bar_time / 1000, IST).date()
        if s.bar_time <= busy_until or per_day.get(day, 0) >= MAX_TRADES_PER_DAY:
            continue  # live bot holds one position at a time and caps trades per day
        r, exit_ts, how = simulate(s, c5)
        busy_until = exit_ts
        per_day[day] = per_day.get(day, 0) + 1
        gross = r * (abs(s.entry - s.stop) / s.entry) * NOTIONAL_INR
        fees = 2 * FEE_PCT / 100 * NOTIONAL_INR
        net = gross - fees
        equity += net
        rows.append({"signal_time_ist": fmt(s.bar_time), "side": s.side, "entry": round(s.entry, 1),
                     "stop": round(s.stop, 1), "target": round(s.target, 1), "risk_pct": round(s.risk_pct, 3),
                     "result": how, "R": round(r, 2), "exit_time_ist": fmt(exit_ts),
                     "gross_inr": round(gross, 1), "fees_inr": round(fees, 1), "net_inr": round(net, 1)})
    wins = [x for x in rows if x["R"] > 0]
    total_r = sum(x["R"] for x in rows)
    lines = [
        f"BTC SMC BACKTEST - last {DAYS} days ({fmt(start_ms)} to {fmt(c5[-1]['timestamp'])} IST), {PAIR}",
        f"Candles used: {len(c5)} x 5m, {len(c1)} x 1H | notional Rs{NOTIONAL_INR:.0f} (margin x leverage) | fee assumption {FEE_PCT}%/side",
        f"Trades: {len(rows)} | wins: {len(wins)} | losses: {len(rows) - len(wins)} | win rate: {100 * len(wins) / len(rows):.0f}%" if rows else "Trades: 0",
    ]
    if rows:
        by = {}
        for x in rows:
            by[x["result"]] = by.get(x["result"], 0) + 1
        lines += [f"Total R: {total_r:+.2f} | avg R/trade: {total_r / len(rows):+.2f} | exits: {by}",
                  f"Gross P/L: Rs{sum(x['gross_inr'] for x in rows):+.0f} | fees: Rs{sum(x['fees_inr'] for x in rows):.0f} | NET: Rs{equity:+.0f}"]
        peak = dd = run = 0.0
        for x in rows:
            run += x["net_inr"]; peak = max(peak, run); dd = max(dd, peak - run)
        lines.append(f"Max drawdown (net): Rs{dd:.0f}")
    if rows:
        monthly = {}
        for x in rows:
            m = x["signal_time_ist"][3:6] if False else datetime.strptime(x["signal_time_ist"], "%d-%b %H:%M").strftime("%b")
            a = monthly.setdefault(m, [0, 0.0, 0.0]); a[0] += 1; a[1] += x["R"]; a[2] += x["net_inr"]
        lines.append("By month: " + " | ".join(f"{m}: {a[0]} trades {a[1]:+.1f}R Rs{a[2]:+.0f}" for m, a in monthly.items()))
        lg = sum(x["R"] for x in rows if x["R"] > 0); ls = -sum(x["R"] for x in rows if x["R"] < 0)
        lines.append(f"Profit factor (R): {lg / ls:.2f}" if ls else "Profit factor: n/a (no losses)")
    lines.append("")
    lines += [f"{x['signal_time_ist']} {x['side']:5} entry {x['entry']} SL {x['stop']} -> {x['result']} {x['R']:+.2f}R  net Rs{x['net_inr']:+.0f}" for x in rows]
    lines += ["", "Notes: entry assumed at signal-bar close (live uses a market order, so real fills differ by slippage); stop is checked before target inside the same candle; past results do not guarantee future results."]
    out = Path("data"); out.mkdir(exist_ok=True)
    (out / f"backtest_{DAYS}d.txt").write_text("\n".join(lines) + "\n")
    if rows:
        with (out / f"backtest_{DAYS}d.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
