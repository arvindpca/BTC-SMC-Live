"""
PAXG/USDT backtest: RSI mean reversion + 200 EMA trend filter + ADX range filter
Data: CoinDCX public candles (fallback: Binance PAXGUSDT, ya apni CSV)

Install:   pip install pandas numpy requests matplotlib
Run:       python paxg_backtest.py                      (CoinDCX, 1h, ~2 saal)
           python paxg_backtest.py --source binance
           python paxg_backtest.py --source csv --csv data.csv
CSV columns: time(ms ya datetime), open, high, low, close, volume

Disclaimer: educational. Backtest future ki guarantee nahi hai.
"""
import argparse
import time

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------- DATA ----
INTERVAL_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


def fetch_coindcx(pair="B-PAXG_USDT", interval="1h", days=730):
    """CoinDCX public candles. Pair format aam taur par B-<COIN>_USDT hota hai.
    Agar error aaye to https://api.coindcx.com/exchange/v1/markets_details se
    sahi 'pair' naam check karo."""
    url = "https://public.coindcx.com/market_data/candles"
    end = int(time.time() * 1000)
    start_limit = end - days * 86_400_000
    rows = []
    while end > start_limit:
        r = requests.get(url, params={"pair": pair, "interval": interval,
                                      "endTime": end, "limit": 1000}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows.extend(data)
        oldest = min(d["time"] for d in data)
        if oldest >= end:
            break
        end = oldest - 1
        time.sleep(0.3)
    if not rows:
        raise RuntimeError("CoinDCX se data nahi mila. Pair naam check karo ya --source binance try karo.")
    df = pd.DataFrame(rows)
    return _clean(df)


def fetch_binance(symbol="PAXGUSDT", interval="1h", days=730):
    url = "https://api.binance.com/api/v3/klines"
    end = int(time.time() * 1000)
    start_limit = end - days * 86_400_000
    rows = []
    while end > start_limit:
        r = requests.get(url, params={"symbol": symbol, "interval": interval,
                                      "endTime": end, "limit": 1000}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows = data + rows
        end = data[0][0] - 1
        time.sleep(0.2)
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]
    return _clean(df)


def _clean(df):
    df = df.copy()
    if pd.api.types.is_numeric_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="ms")
    else:
        df["time"] = pd.to_datetime(df["time"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True)
    return df[["time", "open", "high", "low", "close", "volume"]]


# ---------------------------------------------------------- INDICATORS ----
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df, p):
    df = df.copy()
    df["rsi"] = rsi(df["close"], p["rsi_len"])
    df["ema200"] = ema(df["close"], p["trend_ema"])
    df["ema20"] = ema(df["close"], p["exit_ema"])
    df["atr"] = atr(df, p["atr_len"])
    df["adx"] = adx(df, p["adx_len"])
    df["signal"] = ((df["rsi"] < p["rsi_buy"]) &
                    (df["close"] > df["ema200"]) &
                    (df["adx"] < p["adx_max"]))
    return df


# ------------------------------------------------------------ BACKTEST ----
def backtest(df, p):
    """Long-only. Signal candle close par banta hai, entry agli candle ke open par.
    SL candle ke low se check hota hai. Exit: RSI > exit_rsi ya close >= EMA20."""
    o, h, l, c = (df[k].values for k in ["open", "high", "low", "close"])
    rs, e20, at, sig = df["rsi"].values, df["ema20"].values, df["atr"].values, df["signal"].values
    t = df["time"].values
    fee, slip = p["fee"], p["slippage"]

    eq = p["capital"]
    pos = None
    trades, curve = [], [(t[0], eq)]
    for i in range(1, len(df)):
        # --- entry
        if pos is None and sig[i - 1] and not np.isnan(at[i - 1]):
            entry = o[i] * (1 + slip)
            stop = entry - p["sl_atr"] * at[i - 1]
            dist = entry - stop
            if dist > 0:
                qty = eq * p["risk_pct"] / dist
                qty = min(qty, eq * p["max_leverage"] / entry)
                pos = dict(i=i, entry=entry, stop=stop, qty=qty)
                eq -= qty * entry * fee
        # --- manage
        if pos is not None:
            exit_px, reason = None, None
            if l[i] <= pos["stop"]:
                exit_px, reason = min(o[i], pos["stop"]) * (1 - slip), "SL"
            elif rs[i] > p["rsi_exit"] or c[i] >= e20[i]:
                exit_px, reason = c[i] * (1 - slip), "SIGNAL"
            if exit_px is not None:
                pnl = (exit_px - pos["entry"]) * pos["qty"] - pos["qty"] * exit_px * fee
                eq += (exit_px - pos["entry"]) * pos["qty"] - pos["qty"] * exit_px * fee
                trades.append(dict(entry_time=t[pos["i"]], exit_time=t[i], entry=pos["entry"],
                                   exit=exit_px, pnl=pnl - pos["qty"] * pos["entry"] * fee,
                                   reason=reason, bars=i - pos["i"]))
                curve.append((t[i], eq))
                pos = None
    return pd.DataFrame(trades), pd.DataFrame(curve, columns=["time", "equity"])


def stats(trades, curve, label, capital):
    if trades.empty:
        print(f"\n[{label}] Koi trade nahi bana.")
        return {}
    w = trades[trades.pnl > 0]
    lo = trades[trades.pnl <= 0]
    gp, gl = w.pnl.sum(), -lo.pnl.sum()
    pf = gp / gl if gl > 0 else np.inf
    wr = len(w) / len(trades) * 100
    aw = w.pnl.mean() if len(w) else 0.0
    al = lo.pnl.mean() if len(lo) else 0.0
    exp = trades.pnl.mean()
    peak = curve.equity.cummax()
    dd = ((curve.equity - peak) / peak).min() * 100
    streak = cur = 0
    for x in (trades.pnl <= 0):
        cur = cur + 1 if x else 0
        streak = max(streak, cur)
    ret = (curve.equity.iloc[-1] / capital - 1) * 100
    print(f"\n===== {label} =====")
    print(f"Trades            : {len(trades)}")
    print(f"Win rate          : {wr:.1f}%")
    print(f"Profit factor     : {pf:.2f}")
    print(f"Avg win / Avg loss: {aw:.2f} / {al:.2f}  (ratio {abs(aw / al) if al else float('nan'):.2f})")
    print(f"Expectancy/trade  : {exp:.2f} USDT")
    print(f"Total return      : {ret:.1f}%")
    print(f"Max drawdown      : {dd:.1f}%  (closed-trade basis)")
    print(f"Max losing streak : {streak}")
    print(f"SL exits          : {(trades.reason == 'SL').mean() * 100:.0f}%")
    if len(trades) < 100:
        print("!! Trades 100 se kam hain, result bharosemand nahi. Zyada data ya 15m try karo.")
    return dict(trades=len(trades), win=wr, pf=pf, ret=ret, dd=dd)


# ---------------------------------------------------------------- MAIN ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="coindcx", choices=["coindcx", "binance", "csv"])
    ap.add_argument("--pair", default="B-PAXG_USDT")
    ap.add_argument("--interval", default="1h", choices=list(INTERVAL_MS))
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--csv")
    ap.add_argument("--split", type=float, default=0.7, help="train fraction")
    ap.add_argument("--rsi-buy", type=float, default=30, help="entry RSI level (35 = zyada trades)")
    ap.add_argument("--adx-max", type=float, default=25, help="ADX upper limit (range filter)")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()

    p = dict(rsi_len=14, rsi_buy=a.rsi_buy, rsi_exit=50, trend_ema=200, exit_ema=20,
             atr_len=14, adx_len=14, adx_max=a.adx_max, sl_atr=2.0,
             fee=0.001, slippage=0.0002, capital=1000.0, risk_pct=0.01, max_leverage=1.0)

    if a.source == "csv":
        df = _clean(pd.read_csv(a.csv))
    elif a.source == "binance":
        df = fetch_binance("PAXGUSDT", a.interval, a.days)
    else:
        df = fetch_coindcx(a.pair, a.interval, a.days)
    print(f"Data: {len(df)} candles, {df.time.iloc[0]} -> {df.time.iloc[-1]}")

    df = add_indicators(df, p)
    df = df.iloc[p["trend_ema"]:].reset_index(drop=True)  # warm-up hatao
    cut = int(len(df) * a.split)
    train, test = df.iloc[:cut].reset_index(drop=True), df.iloc[cut:].reset_index(drop=True)

    res = {}
    for name, d in [("TRAIN (in-sample)", train), ("TEST (out-of-sample)", test), ("FULL", df)]:
        tr, cv = backtest(d, p)
        res[name] = (tr, cv)
        stats(tr, cv, name, p["capital"])

    res["FULL"][0].to_csv("paxg_trades.csv", index=False)
    print("\nTrades save hue: paxg_trades.csv")
    print("Dhyan do: TEST ka result TRAIN jaisa na aaye to strategy overfit/unstable hai.")

    if not a.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(10, 4))
            cv = res["FULL"][1]
            ax.plot(cv.time, cv.equity)
            ax.axvline(test.time.iloc[0], color="r", ls="--", label="train | test")
            ax.set_title("PAXG/USDT equity curve (closed trades)")
            ax.legend()
            fig.tight_layout()
            fig.savefig("paxg_equity.png", dpi=120)
            print("Equity curve save hui: paxg_equity.png")
        except ImportError:
            print("matplotlib nahi mila, plot skip.")


if __name__ == "__main__":
    main()
