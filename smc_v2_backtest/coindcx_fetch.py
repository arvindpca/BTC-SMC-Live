"""Read-only: CoinDCX FUTURES 15m candles (public endpoint, no keys, no orders).

Endpoint (same one app/market_data.py uses): https://public.coindcx.com/market_data/candlesticks
Saves <SYMBOL>_15m_cache.csv (time in ms, open, high, low, close, volume) into --out, e.g.
  B-BTC_USDT  -> BTCUSDT_15m_cache.csv
  B-PAXG_USDT -> PAXGUSDT_15m_cache.csv   (only if CoinDCX lists a PAXG futures instrument)
Tries resolution "15"; if CoinDCX rejects it, falls back to "5" and resamples to 15m.
"""
import argparse
import os
import sys
import time

import pandas as pd
import requests

BASE = "https://public.coindcx.com/market_data/candlesticks"
ACTIVE = "https://api.coindcx.com/exchange/v1/derivatives/futures/data/active_instruments"
SEC = {"5": 300, "15": 900}


def active(margin):
    r = requests.get(ACTIVE, params={"margin_currency_short_name[]": margin}, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch(pair, res, days, verbose=True):
    sec = SEC[res]
    now = int(time.time()) // sec * sec
    start = now - days * 86400
    out, to, empty, first = {}, now, 0, True
    while to > start:
        frm = max(start, to - 500 * sec)
        try:
            r = requests.get(BASE, params={"pair": pair, "from": frm, "to": to, "resolution": res, "pcode": "f"},
                             timeout=20)
            j = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"  {pair} res={res}: request error {exc}")
            break
        if first and verbose:
            print(f"  {pair} res={res}: HTTP {r.status_code} status={j.get('s') if isinstance(j, dict) else type(j)}")
            first = False
        if not isinstance(j, dict) or j.get("s") != "ok":
            break
        data = j.get("data") or []
        for it in data:
            try:
                ts = int(float(it["time"]))
                ts = ts if ts > 10**12 else ts * 1000
                out[ts] = (ts, float(it["open"]), float(it["high"]), float(it["low"]), float(it["close"]),
                           float(it["volume"]))
            except (KeyError, TypeError, ValueError):
                continue
        empty = 0 if data else empty + 1
        if empty >= 3:
            break
        to = frm - 1
        time.sleep(0.15)
    rows = [v for k, v in sorted(out.items()) if k + sec * 1000 <= now * 1000]   # forming candle hata do
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


def to_15m(df5):
    d = df5.copy()
    d["time"] = pd.to_datetime(d["time"], unit="ms")
    d = d.set_index("time")
    g = d.resample("15min", label="left", closed="left")
    r = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    r = r[g["close"].count() == 3].dropna()
    r.index = r.index.astype("int64") // 10**6
    return r.reset_index().rename(columns={"time": "time"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--out", default="data_cache")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    print("=== CoinDCX active futures instruments (PAXG / BTC) ===")
    names = {}
    for m in ("USDT", "INR"):
        try:
            lst = active(m)
            names[m] = lst
            print(f"{m} margin: {len(lst)} instruments | BTC: {[x for x in lst if 'BTC' in x][:6]} | "
                  f"PAXG: {[x for x in lst if 'PAXG' in x]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{m} margin: active_instruments request failed: {exc}")
    paxg = next((x for m in ("USDT", "INR") for x in names.get(m, []) if "PAXG" in x), None)

    targets = [("B-BTC_USDT", "BTCUSDT")]
    if paxg:
        targets.append((paxg, "PAXGUSDT"))
    else:
        print("\n!! CoinDCX futures me PAXG instrument nahi mila -> PAXG skip (sirf BTC).")

    ok_btc = False
    for pair, sym in targets:
        print(f"\n=== {pair} -> {sym}_15m_cache.csv (last {a.days} days) ===")
        df = fetch(pair, "15", a.days)
        if len(df) < 200:
            print("  resolution 15 se data nahi mila, 5m se bana raha hoon...")
            d5 = fetch(pair, "5", a.days)
            df = to_15m(d5) if len(d5) else d5
        if df.empty:
            print(f"  {pair}: koi candle nahi mila.")
            continue
        df.to_csv(os.path.join(a.out, f"{sym}_15m_cache.csv"), index=False)
        t0, t1 = pd.to_datetime(df.time.iloc[0], unit="ms"), pd.to_datetime(df.time.iloc[-1], unit="ms")
        exp = int((t1 - t0) / pd.Timedelta("15min")) + 1
        print(f"  {len(df)} candles | {t0} -> {t1} UTC | missing {exp - len(df)} ({(exp - len(df)) / exp * 100:.1f}%)")
        ok_btc = ok_btc or sym == "BTCUSDT"
    if not ok_btc:
        sys.exit("ERROR: BTC candles nahi mile.")


if __name__ == "__main__":
    main()
