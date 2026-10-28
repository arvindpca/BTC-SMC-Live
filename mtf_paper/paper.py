"""1D trend > 1H zone > 15M entry -- BTC + PAXG PAPER trader (v3).

EDUCATIONAL PURPOSE ONLY. Paper trading only: no API keys, no orders, no real money.
Not financial advice. Backtests and paper results do not guarantee anything. Results are shown GROSS (no fees).

Rules (same as the research backtest, filters F1+F2+F3):
  1. Daily trend : previous completed daily close vs EMA20 -> only that side.
  2. 1H zone     : 1H pivot low/high (2 bars each side), width 0.5 x ATR14(1H), valid 5 days.
  3. 15M trigger : price touches the zone, then within 16 bars a candle closes beyond the previous 3 bars' high (BUY) /
                   low (SELL). Entry = open of the next 15m candle.
  4. Stop = far edge of the zone, target = 2R, time exit after 12h, one position per symbol (after an entry, no new trade on that symbol for 12h).
  5. Filters : stop distance >= MIN_STOP, zone age <= 48h, daily trend strength >= MIN_TREND.
Usage:
  python -m mtf_paper.paper loop 340     # run N minutes, one cycle every 15 min (:02,:17,:32,:47)
  python -m mtf_paper.paper once
  python -m mtf_paper.paper replay NAME CSV [DAYS]   # offline check on cached candles (no notifications)
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

SYMS = {"BTC": dict(pair="B-BTC_USDT", min_stop=0.46, min_trend=1.15),
        "PAXG": dict(pair="B-PAXG_USDT", min_stop=0.23, min_trend=0.57)}   # percent thresholds (learned on train split)
CAPITAL = float(os.getenv("MTF_PAPER_CAPITAL_INR", "3000"))      # per symbol paper account
LEV = float(os.getenv("MTF_PAPER_LEVERAGE", "30"))
FX = float(os.getenv("MTF_PAPER_USDINR", "88"))
FEE, SLIP = 0.00025, 0.0003      # only used for the reference "net" column in trades.csv; messages show GROSS
RR, HOLD_BARS, TRIG_BARS, ZONE_BARS = 2.0, 48, 16, 5 * 96
MIN_D, MAX_D, MAX_AGE_H = 0.0015, 0.04, 48
SEC = 900
MS = SEC * 1000
TAG = "[v3 1D>1H>15M]"
STATE_BRANCH = os.getenv("MTF_STATE_BRANCH", "mtf-paper-state")
STATE_DIR = os.getenv("MTF_STATE_DIR", "paper_state")
BASE = "https://public.coindcx.com/market_data/candlesticks"
REPO = os.getenv("GITHUB_REPOSITORY", "arvindpca/BTC-SMC-Live")
SERVER = os.getenv("GITHUB_SERVER_URL", "https://github.com")
LINK_STATUS = f"{SERVER}/{REPO}/blob/{STATE_BRANCH}/STATUS.md"
LINK_TRADES = f"{SERVER}/{REPO}/blob/{STATE_BRANCH}/trades.csv"
LINK_ACTIONS = f"{SERVER}/{REPO}/actions/workflows/mtf-paper.yml"
DISCLAIMER = "📚 Educational paper trade only - not financial advice. Gross = without fees; fees reduce results."
COLS = ["id", "symbol", "side", "entry_time_utc", "exit_time_utc", "entry", "exit", "stop", "target", "reason", "qty",
        "notional_inr", "gross_pct", "gross_inr", "net_inr_ref", "dist_pct", "trend_str_pct", "zone_age_h", "r_multiple"]


# ----------------------------------------------------------------------------- data
def fetch(pair: str, from_s: int, to_s: int) -> pd.DataFrame:
    out, to = {}, to_s
    while to > from_s:
        frm = max(from_s, to - 500 * SEC)
        j = {}
        for _ in range(3):
            try:
                j = requests.get(BASE, params={"pair": pair, "from": frm, "to": to, "resolution": "15", "pcode": "f"},
                                 timeout=20).json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        if not isinstance(j, dict) or j.get("s") != "ok":
            break
        for it in j.get("data") or []:
            try:
                ts = int(float(it["time"])); ts = ts if ts > 10**12 else ts * 1000
                out[ts] = (ts, float(it["open"]), float(it["high"]), float(it["low"]), float(it["close"]))
            except (KeyError, TypeError, ValueError):
                continue
        to = frm - 1
        time.sleep(0.12)
    return pd.DataFrame(sorted(out.values()), columns=["ts", "open", "high", "low", "close"])


def merge(a: pd.DataFrame, b: pd.DataFrame, keep_days: int = 150) -> pd.DataFrame:
    d = pd.concat([a, b]).drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    return d[d.ts >= d.ts.iloc[-1] - keep_days * 86400 * 1000].reset_index(drop=True)


# ----------------------------------------------------------------------------- formatting
def utc(ms): return datetime.fromtimestamp(ms / 1000, timezone.utc)
def fmt_t(ms):
    u = utc(ms); i = u + timedelta(hours=5, minutes=30)
    return f"{u:%d %b %H:%M} UTC | {i:%d %b %I:%M %p} IST"
def usd(x): return f"${x:,.2f}"
def inr(x): return f"{'+' if x >= 0 else '-'}₹{abs(x):,.0f}"


def notify(text: str) -> None:
    text = f"{TAG} {text}"
    print(text, flush=True)
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=15)
    except Exception as exc:  # noqa: BLE001
        print("telegram error:", exc, flush=True)


def links() -> str:
    run = os.getenv("GITHUB_RUN_ID")
    s = f"🔗 Live tracking: {LINK_STATUS}\n🔗 All trades (csv): {LINK_TRADES}\n🔗 Runner: {LINK_ACTIONS}"
    if run:
        s += f"\n🔗 This run: {SERVER}/{REPO}/actions/runs/{run}"
    return s


# ----------------------------------------------------------------------------- strategy core (stateless recompute)
def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def compute(df: pd.DataFrame, name: str, now_ms: int) -> list[dict]:
    """All signals/trades implied by the candles known at now_ms. status: OPEN or CLOSED. ok = passes F1+F2+F3.
    Uses only CLOSED 15m candles (plus the open price of the candle that is forming, for the entry fill)."""
    cfg = SYMS[name]
    nc = int((df.ts.values + MS <= now_ms).sum())          # df sorted; closed candles come first
    if nc < 3000:
        return []
    forming = df.iloc[nc] if len(df) > nc and int(df.ts.iloc[nc]) == int(df.ts.iloc[nc - 1]) + MS else None
    cl = df.iloc[:nc]
    idx = pd.to_datetime(cl.ts, unit="ms")
    raw = cl.set_index(idx)[["open", "high", "low", "close"]]
    ts = np.append(cl.ts.values, [int(forming.ts)] if forming is not None else [])
    o = np.append(cl.open.values, [float(forming.open)] if forming is not None else [])
    h = np.append(cl.high.values, [np.nan] if forming is not None else [])
    l = np.append(cl.low.values, [np.nan] if forming is not None else [])
    c = np.append(cl.close.values, [np.nan] if forming is not None else [])
    OHLC = {"open": "first", "high": "max", "low": "min", "close": "last"}
    z = raw.resample("1h").agg(OHLC).dropna()
    last_end = raw.index[-1] + pd.Timedelta(minutes=15)
    z = z[z.index + pd.Timedelta("1h") <= last_end]            # only COMPLETE hourly bars
    atz = _atr(z).values; zh, zl = z.high.values, z.low.values; zi = z.index
    dc = raw.close.resample("1D").last().dropna()
    e = dc.ewm(span=20, adjust=False).mean()
    s = np.sign(dc - e).shift(1).fillna(0)
    dstr = ((dc - e) / e * 100).shift(1)
    tsd = pd.to_datetime(ts, unit="ms").values
    zones = []
    for i in range(2, len(z) - 2):
        avail = zi[i + 2] + pd.Timedelta("1h")
        if zl[i] < min(zl[i - 2:i]) and zl[i] < min(zl[i + 1:i + 3]):
            zones.append((avail, 1, zl[i], zl[i] + 0.5 * atz[i + 2]))
        if zh[i] > max(zh[i - 2:i]) and zh[i] > max(zh[i + 1:i + 3]):
            zones.append((avail, -1, zh[i] - 0.5 * atz[i + 2], zh[i]))
    zones.sort(key=lambda q: q[0])
    out, busy = [], -1
    for avail, side, zlo, zhi in zones:
        j0 = int(np.searchsorted(tsd, np.datetime64(avail)))
        if j0 >= nc:
            continue
        if s.get(pd.Timestamp(avail).normalize(), 0) != side:
            continue
        exp = min(nc, j0 + ZONE_BARS); touched = None
        for j in range(max(j0, busy + 1), exp):
            if (side == 1 and c[j] < zlo) or (side == -1 and c[j] > zhi):
                break
            if (side == 1 and l[j] <= zhi) or (side == -1 and h[j] >= zlo):
                touched = j; break
        if touched is None:
            continue
        trig = None
        for k in range(touched, min(touched + TRIG_BARS, nc)):
            if (side == 1 and c[k] < zlo) or (side == -1 and c[k] > zhi):
                break
            if k < 3:
                continue
            if side == 1 and c[k] > o[k] and c[k] > h[k - 3:k].max(): trig = k; break
            if side == -1 and c[k] < o[k] and c[k] < l[k - 3:k].min(): trig = k; break
        if trig is None or trig <= busy:
            continue
        i0 = trig + 1
        if i0 >= len(ts):                      # entry candle not started yet
            continue
        entry = o[i0]; stop = zlo if side == 1 else zhi; dist = side * (entry - stop)
        if not dist > 0 or dist / entry < MIN_D or dist / entry > MAX_D:
            continue
        end = i0 + HOLD_BARS; tp = entry + side * RR * dist
        status, reason, px, kx = "OPEN", "", np.nan, None
        for k in range(i0, min(end, nc - 1) + 1):
            if (side == 1 and l[k] <= stop) or (side == -1 and h[k] >= stop):
                status, reason, px, kx = "CLOSED", "STOP LOSS", stop, k; break
            if (side == 1 and h[k] >= tp) or (side == -1 and l[k] <= tp):
                status, reason, px, kx = "CLOSED", "TARGET (2R)", tp, k; break
        if status == "OPEN" and end <= nc - 1:
            status, reason, px, kx = "CLOSED", "TIME EXIT (12h)", c[end], end
        trend_str = float(dstr.get(pd.Timestamp(avail).normalize(), np.nan)) * side
        age_h = float((tsd[i0] - np.datetime64(avail)) / np.timedelta64(1, "h"))
        dp = dist / entry * 100
        out.append(dict(entry_ts=int(ts[i0]), side=side, entry=float(entry), stop=float(stop), tp=float(tp), dist_pct=dp,
                        trend_str=trend_str, zone_age_h=age_h, zlo=float(zlo), zhi=float(zhi),
                        status=status, reason=reason, exit_px=float(px), exit_ts=(int(ts[kx]) + MS) if kx is not None else None,
                        ok=bool(dp >= cfg["min_stop"] and age_h <= MAX_AGE_H and trend_str >= cfg["min_trend"])))
        busy = end            # same as the backtest: no new trade until the 12h hold window of this one is over
    return out


# ----------------------------------------------------------------------------- journal / stats
def trades_path(): return os.path.join(STATE_DIR, "trades.csv")
def state_path(): return os.path.join(STATE_DIR, "state.json")


def load_trades() -> pd.DataFrame:
    return pd.read_csv(trades_path()) if os.path.exists(trades_path()) else pd.DataFrame(columns=COLS)


def append_trade(row: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    new = not os.path.exists(trades_path())
    with open(trades_path(), "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in COLS})


def stats(t: pd.DataFrame, accounts: int = 1) -> dict:
    cap = CAPITAL * accounts
    if len(t) == 0:
        return dict(n=0, wins=0, wr=0.0, gross=0.0, equity=cap, pf=0.0, dd=0.0, avg_w=0.0, avg_l=0.0, streak=0, best=0.0, worst=0.0)
    g = t.sort_values("exit_time_utc").gross_inr.astype(float)
    eq = np.cumsum(g.values)
    dd = float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    gp, gl = g[g > 0].sum(), -g[g <= 0].sum()
    st = 0
    for v in g.values[::-1]:
        if v <= 0: st += 1
        else: break
    return dict(n=len(g), wins=int((g > 0).sum()), wr=float((g > 0).mean() * 100), gross=float(g.sum()), equity=cap + float(g.sum()),
                pf=float(gp / gl) if gl > 0 else float("inf"), dd=dd, avg_w=float(g[g > 0].mean()) if (g > 0).any() else 0.0,
                avg_l=float(g[g <= 0].mean()) if (g <= 0).any() else 0.0, streak=st, best=float(g.max()), worst=float(g.min()))


def statement(s: dict, title: str, accounts: int = 1) -> str:
    cap = CAPITAL * accounts
    if s["n"] == 0:
        return f"📊 {title}\nClosed trades: 0 | Equity: ₹{cap:,.0f} (start)"
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"📊 {title} (GROSS, closed trades)\n"
            f"Trades: {s['n']} | Wins: {s['wins']} | Win rate: {s['wr']:.1f}%\n"
            f"Gross P/L: {inr(s['gross'])} ({s['gross'] / cap * 100:+.1f}% of capital)\n"
            f"Profit factor: {pf} | Max drawdown: ₹{s['dd']:,.0f}\n"
            f"Avg win {inr(s['avg_w'])} | Avg loss {inr(s['avg_l'])} | Losing streak now: {s['streak']}\n"
            f"Equity: ₹{s['equity']:,.0f} (start ₹{cap:,.0f})")


def full_statement(t: pd.DataFrame) -> str:
    parts = [statement(stats(t[t.symbol == n]), f"{n} account")] if False else []
    for n in SYMS:
        parts.append(statement(stats(t[t.symbol == n]), f"{n} statement"))
    parts.append(statement(stats(t, len(SYMS)), "COMBINED", len(SYMS)))
    return "\n\n".join(parts)


def entry_msg(name: str, p: dict, open_n: int) -> str:
    qty = CAPITAL * LEV / FX / p["entry"]
    risk = qty * abs(p["entry"] - p["stop"]) * FX
    rew = risk * RR
    d, icon = ("BUY / LONG", "🟢") if p["side"] == 1 else ("SELL / SHORT", "🔴")
    zone = "demand" if p["side"] == 1 else "supply"
    liq = p["entry"] * (1 - 1 / LEV * 0.97) if p["side"] == 1 else p["entry"] * (1 + 1 / LEV * 0.97)
    return (f"{icon} PAPER ENTRY | {d} | {name}/USDT\n"
            f"🕒 {fmt_t(p['entry_ts'])}\n"
            f"Entry : {usd(p['entry'])}\n"
            f"Stop  : {usd(p['stop'])} ({p['dist_pct']:.2f}% away)\n"
            f"Target: {usd(p['tp'])} ({p['dist_pct'] * RR:.2f}% away, {RR:g}R)\n"
            f"Size  : {qty:.5f} {name} | ₹{CAPITAL * LEV:,.0f} notional ({LEV:g}x on ₹{CAPITAL:,.0f})\n"
            f"Risk at stop: {inr(-risk)} ({risk / CAPITAL * 100:.0f}% of capital) | Reward at target: {inr(rew)}\n"
            f"Approx. liquidation: {usd(liq)} (stop is well before it)\n"
            f"Why: daily trend {'UP' if p['side'] == 1 else 'DOWN'} (close {p['trend_str']:.2f}% from EMA20) > 1H {zone} zone "
            f"{usd(p['zlo'])}-{usd(p['zhi'])} (age {p['zone_age_h']:.0f}h) > 15m confirmation candle\n"
            f"⏱ Time exit: 12h after entry if stop/target not hit\n"
            f"Open positions now: {open_n}\n{links()}\n{DISCLAIMER}")


def exit_msg(name: str, row: dict, t: pd.DataFrame, open_n: int) -> str:
    g = float(row["gross_inr"]); win = g > 0
    d = "LONG" if int(row["side"]) == 1 else "SHORT"
    dur = (pd.Timestamp(row["exit_time_utc"]) - pd.Timestamp(row["entry_time_utc"])).total_seconds() / 3600
    ex = int(pd.Timestamp(row["exit_time_utc"]).timestamp() * 1000)
    return (f"{'✅' if win else '❌'} PAPER EXIT | {d} | {name}/USDT | {row['reason']}\n"
            f"🕒 {fmt_t(ex)} (held {dur:.1f}h)\n"
            f"Entry {usd(float(row['entry']))} -> Exit {usd(float(row['exit']))} ({float(row['gross_pct']):+.2f}%)\n"
            f"Gross P/L: {inr(g)} | {float(row['r_multiple']):+.2f}R\n\n"
            f"{full_statement(t)}\n\nOpen positions now: {open_n}\n{links()}\n{DISCLAIMER}")


# ----------------------------------------------------------------------------- engine
def fresh_state(now_ms: int) -> dict:
    return dict(live_from_ms=now_ms, started=utc(now_ms).strftime("%Y-%m-%d %H:%M UTC"), n_closed=0, daily_sent="",
                sym={n: dict(announced=[], closed=[], missed=[], open=None) for n in SYMS})


def load_state(now_ms: int):
    if os.path.exists(state_path()):
        s = json.load(open(state_path()))
        for n in SYMS:
            s["sym"].setdefault(n, dict(announced=[], closed=[], missed=[], open=None))
        return s, False
    return fresh_state(now_ms), True


def save_state(s: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(s, open(state_path(), "w"), indent=1)


def open_count(s: dict) -> int:
    return sum(1 for n in SYMS if s["sym"][n]["open"])


def process(s: dict, name: str, df: pd.DataFrame, now_ms: int, emit=notify, persist=append_trade, trades_df=load_trades) -> int:
    """One cycle for one symbol. Returns number of events."""
    ev, st = 0, s["sym"][name]
    for p in compute(df, name, now_ms):
        if not p["ok"] or p["entry_ts"] < s["live_from_ms"]:
            continue
        key = p["entry_ts"]
        if key not in st["announced"] and key not in st["missed"]:
            if st["open"] is None and now_ms - key <= 2 * MS:
                st["announced"].append(key); st["open"] = p; ev += 1
                emit(entry_msg(name, p, open_count(s)))
            elif st["open"] is None or now_ms - key > 2 * MS:
                st["missed"].append(key)           # runner was offline / too late: do not pretend a fill
                if p["status"] == "OPEN":
                    ev += 1
                    emit(f"⚠️ {name} signal at {fmt_t(key)} was found late (runner offline) - not taken.\n{DISCLAIMER}")
                continue
        if key in st["announced"] and key not in st["closed"] and p["status"] == "CLOSED":
            op = st["open"] or p
            qty = CAPITAL * LEV / FX / op["entry"]
            gu = p["side"] * (p["exit_px"] - op["entry"])
            gross = gu * qty * FX
            cost = (FEE + SLIP) * (op["entry"] + p["exit_px"]) * qty * FX
            s["n_closed"] += 1
            row = dict(id=s["n_closed"], symbol=name, side=p["side"], entry_time_utc=utc(key).strftime("%Y-%m-%d %H:%M"),
                       exit_time_utc=utc(p["exit_ts"]).strftime("%Y-%m-%d %H:%M"), entry=round(op["entry"], 2),
                       exit=round(p["exit_px"], 2), stop=round(op["stop"], 2), target=round(op["tp"], 2), reason=p["reason"],
                       qty=round(qty, 6), notional_inr=round(CAPITAL * LEV), gross_pct=round(gu / op["entry"] * 100, 4),
                       gross_inr=round(gross, 2), net_inr_ref=round(gross - cost, 2), dist_pct=round(op["dist_pct"], 3),
                       trend_str_pct=round(op["trend_str"], 3), zone_age_h=round(op["zone_age_h"], 1),
                       r_multiple=round(gu / abs(op["entry"] - op["stop"]), 3))
            persist(row)
            st["closed"].append(key); st["open"] = None; ev += 1
            emit(exit_msg(name, row, trades_df(), open_count(s)))
    for k in ("announced", "closed", "missed"):
        st[k] = st[k][-300:]
    return ev


def daily_msg(s: dict, last: dict) -> str:
    lines = []
    for n in SYMS:
        o = s["sym"][n]["open"]
        if o and n in last:
            u = o["side"] * (last[n] - o["entry"]) * (CAPITAL * LEV / FX / o["entry"]) * FX
            lines.append(f"{n}: {'LONG' if o['side'] == 1 else 'SHORT'} open since {fmt_t(o['entry_ts'])} | entry {usd(o['entry'])} | now {usd(last[n])} | unrealized {inr(u)}")
        else:
            lines.append(f"{n}: no open position")
    return (f"🗓 DAILY SUMMARY | {datetime.now(timezone.utc) + timedelta(hours=5, minutes=30):%d %b %Y} IST\n" + "\n".join(lines) +
            f"\n\n{full_statement(load_trades())}\n{links()}\n{DISCLAIMER}")


# ----------------------------------------------------------------------------- STATUS.md
def write_status(s: dict, last: dict) -> None:
    t = load_trades()
    L = [f"# 1D > 1H > 15M Paper Trader (BTC + PAXG) - live tracking", "",
         "> **EDUCATIONAL PURPOSE ONLY.** Paper trading, no real orders, not financial advice. Results are GROSS (no fees); "
         "fees/slippage reduce results. Backtest and paper results do not guarantee anything.", "",
         f"- Updated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ({datetime.now(timezone.utc) + timedelta(hours=5, minutes=30):%d %b %I:%M %p} IST) | Started: {s.get('started', '?')}",
         f"- Paper account: ₹{CAPITAL:,.0f} per symbol x {LEV:g}x = ₹{CAPITAL * LEV:,.0f} per trade | USD/INR {FX:g}",
         f"- Links: [STATUS]({LINK_STATUS}) | [trades.csv]({LINK_TRADES}) | [runner]({LINK_ACTIONS})", "", "## Open positions"]
    rows = []
    for n in SYMS:
        o = s["sym"][n]["open"]
        if o and n in last:
            u = o["side"] * (last[n] - o["entry"]) * (CAPITAL * LEV / FX / o["entry"]) * FX
            rows.append(f"| {n} | {'LONG' if o['side'] == 1 else 'SHORT'} | {usd(o['entry'])} | {usd(last[n])} | {usd(o['stop'])} | {usd(o['tp'])} | {inr(u)} | {fmt_t(o['entry_ts'])} |")
    L += (["| Symbol | Side | Entry | Now | Stop | Target | Unrealized (gross) | Since |", "|---|---|---|---|---|---|---|---|"] + rows) if rows else ["None"]
    L += ["", "## Statement (closed trades, gross)", "```", full_statement(t), "```", "", "## Month-wise gross P/L (₹)"]
    if len(t):
        t2 = t.copy(); t2["month"] = pd.to_datetime(t2.exit_time_utc).dt.strftime("%Y-%m")
        m = t2.pivot_table(index="month", columns="symbol", values="gross_inr", aggfunc="sum", fill_value=0)
        m["TOTAL"] = m.sum(axis=1)
        L += ["| Month | " + " | ".join(m.columns) + " |", "|---|" + "---|" * len(m.columns)]
        for k, r in m.iterrows():
            L.append(f"| {k} | " + " | ".join(inr(v) for v in r.values) + " |")
    else:
        L.append("No closed trades yet.")
    L += ["", "## Last 25 closed trades", "| # | Sym | Side | Entry (UTC) | Exit (UTC) | Entry | Exit | Reason | Gross ₹ | R |", "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in t.tail(25).iloc[::-1].iterrows():
        L.append(f"| {r.id} | {r.symbol} | {'L' if int(r.side) == 1 else 'S'} | {r.entry_time_utc} | {r.exit_time_utc} | {r.entry} | {r.exit} | {r.reason} | {inr(float(r.gross_inr))} | {float(r.r_multiple):+.2f} |")
    os.makedirs(STATE_DIR, exist_ok=True)
    open(os.path.join(STATE_DIR, "STATUS.md"), "w").write("\n".join(L) + "\n")


def commit_state(msg: str) -> None:
    if not os.path.isdir(os.path.join(STATE_DIR, ".git")):
        return
    cmd = ["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
    subprocess.run(["git", "add", "-A"], cwd=STATE_DIR)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=STATE_DIR).returncode == 0:
        return
    subprocess.run(cmd + ["commit", "-q", "-m", msg], cwd=STATE_DIR)
    r = subprocess.run(["git", "push", "-q", "origin", f"HEAD:{STATE_BRANCH}"], cwd=STATE_DIR, capture_output=True, text=True)
    if r.returncode:
        subprocess.run(["git", "pull", "-q", "--rebase", "origin", STATE_BRANCH], cwd=STATE_DIR, capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", f"HEAD:{STATE_BRANCH}"], cwd=STATE_DIR)


# ----------------------------------------------------------------------------- runners
def run_loop(minutes: int) -> None:
    now = int(time.time())
    data = {n: fetch(c["pair"], now - 150 * 86400, now) for n, c in SYMS.items()}
    for n, d in data.items():
        if len(d) < 8000:
            sys.exit(f"not enough candles for {n} ({len(d)}) - API problem?")
    s, first = load_state(now * 1000)
    if first:
        notify("🚀 PAPER TRADER STARTED | BTC + PAXG | 1D trend > 1H zone > 15M entry\n"
               f"🕒 {fmt_t(now * 1000)}\n"
               f"Paper account ₹{CAPITAL:,.0f} per symbol x {LEV:g}x = ₹{CAPITAL * LEV:,.0f} per trade.\n"
               f"Rules: daily EMA20 trend, 1H pivot zone, 15m confirmation, stop = zone far edge, target {RR:g}R, time exit 12h, "
               f"filters: stop >= BTC 0.46% / PAXG 0.23%, zone age <= 48h, trend strength >= BTC 1.15% / PAXG 0.57%.\n"
               f"Messages: entry, exit (with statement), daily summary at 12:00 AM IST.\n{links()}\n{DISCLAIMER}")
    end, last_commit = time.time() + minutes * 60, 0.0
    while True:
        now = int(time.time()); ev = 0; last = {}
        for n, c in SYMS.items():
            try:
                data[n] = merge(data[n], fetch(c["pair"], now - 3 * 86400, now))
                ev += process(s, n, data[n], now * 1000)
                last[n] = float(data[n].close.iloc[-1])
            except Exception as exc:  # noqa: BLE001
                print(f"{n} cycle error: {exc}", flush=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute >= 18 * 60 + 30 and s.get("daily_sent") != today:
            s["daily_sent"] = today; ev += 1
            notify(daily_msg(s, last))
        save_state(s); write_status(s, last)
        if ev or time.time() - last_commit > 3600:
            commit_state("paper: update status/journal"); last_commit = time.time()
        nxt = (int(time.time()) // SEC + 1) * SEC + 120
        if nxt > end:
            break
        time.sleep(max(1, nxt - time.time()))
    commit_state("paper: end of run")


def replay(name: str, path: str, days: int = 4) -> None:
    """Offline check: step through the last `days` days in 15-min steps with the real engine; messages are collected."""
    global STATE_DIR
    import tempfile
    STATE_DIR = tempfile.mkdtemp()
    d = pd.read_csv(path).rename(columns={"time": "ts"})[["ts", "open", "high", "low", "close"]].sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    last_ts = int(d.ts.iloc[-1]); start = last_ts - days * 86400000
    s = fresh_state(start)
    msgs = []
    t0 = time.time()
    for now in range(start, last_ts + MS, MS):
        sub = d[(d.ts <= now)].tail(150 * 96)
        process(s, name, sub, now + 2 * 60000, emit=msgs.append)
    print(f"replay {name}: {len(msgs)} messages in {time.time() - t0:.0f}s; closed={s['n_closed']}")
    for m in msgs[:3]:
        print("-----\n" + m)
    t = load_trades(); print(t[["symbol", "side", "entry_time_utc", "exit_time_utc", "reason", "gross_pct", "gross_inr"]].to_string())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "loop":
        run_loop(int(sys.argv[2]) if len(sys.argv) > 2 else 340)
    elif cmd == "replay":
        replay(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    else:
        run_loop(0)
