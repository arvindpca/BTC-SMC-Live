"""PAXG "S1 London breakout" PAPER trader.

EDUCATIONAL PURPOSE ONLY. Paper trading only: no API keys, no orders, no real money.
Not financial advice. Backtests and paper results do not guarantee anything.

Rules (same as the research backtest):
  * Asia range = high/low of 00:00-07:00 UTC, Mon-Fri, max 1 trade/day.
  * Trend = previous completed daily close vs daily EMA20 (above -> long only, below -> short only).
  * Signal = first 5m CLOSE beyond the range in the trend direction, 07:00-12:00 UTC.
  * Direction confirmation (v2): last completed 4h close must agree with the trade side (4h close vs 4h EMA20) AND the
    signal candle must close at least 0.5 x ATR(14) beyond the range edge; otherwise the day is skipped.
  * Entry = open of the next candle (paper limit/maker fill). Stop = opposite side of the Asia range
    (skip if 0.3% < distance < 3% is violated). Target = 1.5 x stop distance. Time exit = 16:00 UTC. Runs on 5-minute candles.
Usage:
  python -m paxg_paper.paper loop 340      # run for N minutes, one cycle every 5 min (:02,:07,:12,...)
  python -m paxg_paper.paper once          # a single cycle
  python -m paxg_paper.paper replay FILE   # validate the logic on a CSV of 15m candles (no notifications)
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

PAIR = os.getenv("PAXG_PAIR", "B-PAXG_USDT")
CAPITAL = float(os.getenv("PAXG_PAPER_CAPITAL_INR", "3000"))
LEV = float(os.getenv("PAXG_PAPER_LEVERAGE", "10"))
FX = float(os.getenv("PAXG_PAPER_USDINR", "88"))
FEE, SLIP = 0.00025, 0.0003          # per side: maker fee + slippage (paper assumption)
RR, K_ATR, MIN_D, MAX_D = 1.5, 0.5, 0.003, 0.03
RES_MIN = 5                           # candle size in minutes (research backtest used 5m)
LAST_M = 960 - RES_MIN                # minute-of-day of the last candle before the 16:00 UTC time exit
STATE_DIR = os.getenv("PAXG_STATE_DIR", "paper_state")
BASE = "https://public.coindcx.com/market_data/candlesticks"
SEC = RES_MIN * 60
MS = SEC * 1000
DISCLAIMER = "📚 Educational paper trade only - not financial advice."
TRADE_COLS = ["id", "side", "entry_time_utc", "exit_time_utc", "entry", "exit", "stop", "target", "reason",
              "qty_paxg", "notional_inr", "gross_usd_per_unit", "gross_inr", "cost_inr", "net_inr",
              "gross_pct", "brk_atr", "dist_pct"]


# ----------------------------------------------------------------------------- data
def fetch(from_s: int, to_s: int) -> pd.DataFrame:
    out, to = {}, to_s
    while to > from_s:
        frm = max(from_s, to - 500 * SEC)
        r = requests.get(BASE, params={"pair": PAIR, "from": frm, "to": to, "resolution": str(RES_MIN), "pcode": "f"},
                         timeout=20)
        j = r.json()
        if not isinstance(j, dict) or j.get("s") != "ok":
            break
        for it in j.get("data") or []:
            try:
                ts = int(float(it["time"]))
                ts = ts if ts > 10**12 else ts * 1000
                out[ts] = (ts, float(it["open"]), float(it["high"]), float(it["low"]), float(it["close"]),
                           float(it["volume"]))
            except (KeyError, TypeError, ValueError):
                continue
        to = frm - 1
        time.sleep(0.12)
    return pd.DataFrame(sorted(out.values()), columns=["ts", "open", "high", "low", "close", "volume"])


def merge(a: pd.DataFrame, b: pd.DataFrame, keep_days: int = 130) -> pd.DataFrame:
    d = pd.concat([a, b]).drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    return d[d.ts >= d.ts.iloc[-1] - keep_days * 86400 * 1000].reset_index(drop=True)


# ----------------------------------------------------------------------------- formatting
def utc(ms): return datetime.fromtimestamp(ms / 1000, timezone.utc)
def ist(dt): return dt + timedelta(hours=5, minutes=30)
def fmt_t(ms):
    u = utc(ms); i = ist(u)
    return f"{u:%d %b %H:%M} UTC ({i:%I:%M %p} IST)"
def usd(x): return f"${x:,.2f}"
def inr(x): return f"{'+' if x >= 0 else '-'}₹{abs(x):,.0f}"


def notify(text: str) -> None:
    print(text, flush=True)
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=15)
    except Exception as exc:  # noqa: BLE001
        print("telegram error:", exc, flush=True)


# ----------------------------------------------------------------------------- journal / stats
def trades_path(): return os.path.join(STATE_DIR, "trades.csv")
def state_path(): return os.path.join(STATE_DIR, "state.json")


def load_trades() -> pd.DataFrame:
    if os.path.exists(trades_path()):
        return pd.read_csv(trades_path())
    return pd.DataFrame(columns=TRADE_COLS)


def append_trade(row: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    new = not os.path.exists(trades_path())
    with open(trades_path(), "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_COLS})


def stats(t: pd.DataFrame) -> dict:
    n = len(t)
    if n == 0:
        return dict(n=0, wins_g=0, wins_n=0, wr_g=0.0, wr_n=0.0, gross=0.0, net=0.0, equity=CAPITAL, pf=0.0,
                    dd=0.0, avg_w=0.0, avg_l=0.0, best=0.0, worst=0.0, streak=0)
    g, nt = t.gross_inr.astype(float), t.net_inr.astype(float)
    eq = np.cumsum(nt.values)
    dd = float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    gp, gl = nt[nt > 0].sum(), -nt[nt <= 0].sum()
    s = 0
    for v in nt.values[::-1]:
        if v <= 0: s += 1
        else: break
    return dict(n=n, wins_g=int((g > 0).sum()), wins_n=int((nt > 0).sum()), wr_g=float((g > 0).mean() * 100),
                wr_n=float((nt > 0).mean() * 100), gross=float(g.sum()), net=float(nt.sum()),
                equity=CAPITAL + float(nt.sum()), pf=float(gp / gl) if gl > 0 else float("inf"), dd=dd,
                avg_w=float(nt[nt > 0].mean()) if (nt > 0).any() else 0.0,
                avg_l=float(nt[nt <= 0].mean()) if (nt <= 0).any() else 0.0,
                best=float(nt.max()), worst=float(nt.min()), streak=s)


def statement(s: dict, open_n: int) -> str:
    if s["n"] == 0:
        return f"📊 STATEMENT\nClosed trades: 0 | Open positions: {open_n}\nEquity: ₹{CAPITAL:,.0f} (start)"
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"📊 STATEMENT (closed trades)\n"
            f"Trades: {s['n']} | Wins: {s['wins_g']} gross / {s['wins_n']} net\n"
            f"Win rate: {s['wr_g']:.1f}% (gross) | {s['wr_n']:.1f}% (net)\n"
            f"Gross P/L: {inr(s['gross'])} | Net P/L: {inr(s['net'])}\n"
            f"Profit factor (net): {pf} | Max drawdown: ₹{s['dd']:,.0f}\n"
            f"Avg win {inr(s['avg_w'])} | Avg loss {inr(s['avg_l'])} | Losing streak now: {s['streak']}\n"
            f"Equity: ₹{s['equity']:,.0f} (start ₹{CAPITAL:,.0f}) | Open positions: {open_n}")


def entry_msg(p: dict) -> str:
    risk = p["qty"] * abs(p["entry"] - p["stop"]) * FX
    rew = p["qty"] * abs(p["tp"] - p["entry"]) * FX
    icon = "🟢" if p["side"] == 1 else "🔴"
    d = "LONG" if p["side"] == 1 else "SHORT"
    brk = "above Asia High" if p["side"] == 1 else "below Asia Low"
    return (f"{icon} PAPER ENTRY | {d} | PAXG/USDT\n"
            f"🕒 {fmt_t(p['entry_ts'])}\n"
            f"Entry : {usd(p['entry'])}\n"
            f"Stop  : {usd(p['stop'])} ({p['dist_pct']:.2f}% away)\n"
            f"Target: {usd(p['tp'])} ({p['dist_pct'] * RR:.2f}% away, {RR}x stop)\n"
            f"Size  : {p['qty']:.4f} PAXG | ₹{CAPITAL * LEV:,.0f} notional ({LEV:g}x on ₹{CAPITAL:,.0f})\n"
            f"Risk at stop: {inr(-risk)} ({risk / CAPITAL * 100:.0f}% of capital) | Reward at target: {inr(rew)}\n"
            f"Why: {RES_MIN}m close {brk} (Asia H {usd(p['rh'])} / L {usd(p['rl'])}) | daily trend "
            f"{'UP' if p['side'] == 1 else 'DOWN'} (EMA20) | 4h trend agrees | breakout {p['brk']:.2f} ATR beyond edge (>= {K_ATR})\n"
            f"⏱ Time exit: 16:00 UTC (9:30 PM IST) if stop/target not hit\n"
            f"Open positions: 1\n{DISCLAIMER}")


def exit_msg(row: dict, s: dict) -> str:
    win = float(row["net_inr"]) > 0
    d = "LONG" if row["side"] == 1 else "SHORT"
    dur = (pd.Timestamp(row["exit_time_utc"]) - pd.Timestamp(row["entry_time_utc"])).total_seconds() / 3600
    return (f"{'✅' if win else '❌'} PAPER EXIT | {d} | PAXG/USDT | {row['reason']}\n"
            f"🕒 {fmt_t(int(pd.Timestamp(row['exit_time_utc']).timestamp() * 1000))} (held {dur:.1f}h)\n"
            f"Entry {usd(float(row['entry']))} -> Exit {usd(float(row['exit']))}\n"
            f"Gross P/L: {float(row['gross_usd_per_unit']):+.2f} $/PAXG | {inr(float(row['gross_inr']))}\n"
            f"Fees+slippage: -₹{float(row['cost_inr']):,.0f} | Net P/L: {inr(float(row['net_inr']))}\n\n"
            f"{statement(s, 0)}\n{DISCLAIMER}")


# ----------------------------------------------------------------------------- core logic
class Engine:
    def __init__(self, state: dict, emit=notify, persist_trade=append_trade):
        self.s, self.emit, self.persist_trade = state, emit, persist_trade
        self.asia_cache, self.trend_cache, self.events = {}, {}, 0

    # trend = previous completed daily close vs daily EMA20
    def trend(self, day: str, df: pd.DataFrame) -> int:
        if day not in self.trend_cache:
            d = df.set_index(pd.to_datetime(df.ts, unit="ms")).close.resample("1D").last().dropna()
            d = d[d.index < pd.Timestamp(day)]
            self.trend_cache[day] = int(np.sign(d.iloc[-1] - d.ewm(span=20, adjust=False).mean().iloc[-1])) \
                if len(d) >= 30 else 0
        return self.trend_cache[day]

    @staticmethod
    def atr(df: pd.DataFrame) -> np.ndarray:
        c, h, l = df.close, df.high, df.low
        pc = c.shift(1).fillna(c.iloc[0])
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / 14, adjust=False).mean().values

    def h4_sign(self, df: pd.DataFrame, ts_ms: int) -> int:
        """Sign of (4h close - 4h EMA20) for the last COMPLETED 4h bar before the bar containing ts_ms."""
        b = df.set_index(pd.to_datetime(df.ts, unit="ms"))[["open", "high", "low", "close"]].resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        ema = b.close.ewm(span=20, adjust=False).mean()
        prev = b.index[b.index < pd.Timestamp(ts_ms, unit="ms").floor("4h")]
        if len(prev) < 30:
            return 0
        return int(np.sign(b.close[prev[-1]] - ema[prev[-1]]))

    def asia(self, day: str, a: dict):
        if day not in self.asia_cache:
            m = (a["day"] == day) & (a["mins"] < 420)
            self.asia_cache[day] = (a["h"][m].max(), a["l"][m].min(), int(m.sum())) if m.any() else (None, None, 0)
        return self.asia_cache[day]

    def _open(self, p: dict, o: float, ts: int):
        s = self.s
        if ts < s["live_from_ms"]:
            s["day_note"] = "signal seen before the paper trader was running - skipped"
            s["pending"] = None
            return
        stop = p["rl"] if p["side"] == 1 else p["rh"]
        dist = p["side"] * (o - stop)
        if not dist > 0 or not (MIN_D <= dist / o <= MAX_D):
            s["day_note"] = f"stop distance {dist / o * 100:.2f}% outside 0.3%-3% -> skipped"
            s["pending"] = None
            return
        qty = CAPITAL * LEV / FX / o
        pos = dict(side=p["side"], entry=o, stop=stop, tp=o + p["side"] * RR * dist, entry_ts=ts, qty=qty,
                   dist_pct=dist / o * 100, rh=p["rh"], rl=p["rl"], brk=p["brk"])
        s["pos"], s["pending"] = pos, None
        s["day_note"] = f"{'LONG' if pos['side'] == 1 else 'SHORT'} open"
        self.events += 1
        self.emit(entry_msg(pos))

    def _close(self, px: float, ts: int, reason: str):
        s, p = self.s, self.s["pos"]
        gross_u = p["side"] * (px - p["entry"])
        gross = gross_u * p["qty"] * FX
        cost = (FEE + SLIP) * (p["entry"] + px) * p["qty"] * FX
        t = load_trades() if self.persist_trade is append_trade else None
        tid = (len(t) + 1) if t is not None else s.get("n_closed", 0) + 1
        row = dict(id=tid, side=p["side"], entry_time_utc=utc(p["entry_ts"]).strftime("%Y-%m-%d %H:%M"),
                   exit_time_utc=utc(ts).strftime("%Y-%m-%d %H:%M"), entry=round(p["entry"], 2), exit=round(px, 2),
                   stop=round(p["stop"], 2), target=round(p["tp"], 2), reason=reason, qty_paxg=round(p["qty"], 6),
                   notional_inr=round(CAPITAL * LEV), gross_usd_per_unit=round(gross_u, 2), gross_inr=round(gross, 2),
                   cost_inr=round(cost, 2), net_inr=round(gross - cost, 2), gross_pct=round(gross_u / p["entry"] * 100, 4),
                   brk_atr=round(p["brk"], 3), dist_pct=round(p["dist_pct"], 3))
        self.persist_trade(row)
        s["n_closed"] = s.get("n_closed", 0) + 1
        s["pos"] = None
        s["day_note"] = f"closed {reason}: net {inr(gross - cost)}"
        self.events += 1
        st = stats(load_trades()) if t is not None else stats(pd.DataFrame([row]))
        self.emit(exit_msg(row, st))

    def step(self, df: pd.DataFrame, now_ms: int):
        s = self.s
        ts_a = df.ts.values
        dt = pd.to_datetime(df.ts, unit="ms")
        a = dict(ts=ts_a, o=df.open.values, h=df.high.values, l=df.low.values, c=df.close.values,
                 v=df.volume.values, day=dt.dt.strftime("%Y-%m-%d").values, mins=(dt.dt.hour * 60 + dt.dt.minute).values,
                 dow=dt.dt.dayofweek.values, atr=self.atr(df))
        closed = np.flatnonzero(ts_a + MS <= now_ms)
        for i in closed:
            ts = int(ts_a[i])
            if ts <= s["last_ts"]:
                continue
            day, m = a["day"][i], int(a["mins"][i])
            if day != s["day"]:
                s.update(day=day, day_done=bool(a["dow"][i] >= 5), day_note="weekend - no trading" if a["dow"][i] >= 5 else "",
                         pending=None)
            if s["pending"] and ts == s["pending"]["entry_ts"]:
                self._open(s["pending"], float(a["o"][i]), ts)
            elif s["pending"] and ts > s["pending"]["entry_ts"]:
                s["pending"], s["day_note"] = None, "entry candle missing - skipped"
            p = s["pos"]
            if p and ts >= p["entry_ts"]:
                stop_hit = a["l"][i] <= p["stop"] if p["side"] == 1 else a["h"][i] >= p["stop"]
                tp_hit = a["h"][i] >= p["tp"] if p["side"] == 1 else a["l"][i] <= p["tp"]
                if stop_hit:
                    if ts == p["entry_ts"]:
                        px = p["stop"]
                    else:
                        px = min(a["o"][i], p["stop"]) if p["side"] == 1 else max(a["o"][i], p["stop"])
                    self._close(float(px), ts + MS, "STOP LOSS")
                elif tp_hit:
                    self._close(p["tp"], ts + MS, "TARGET")
                elif m >= LAST_M:
                    self._close(float(a["c"][i]), ts + MS, "TIME EXIT 16:00 UTC")
            if (not s["pos"]) and (not s["pending"]) and (not s["day_done"]) and 420 <= m < 720:
                rh, rl, n = self.asia(day, a)
                if n < int(0.8 * 420 / RES_MIN):
                    s["day_done"], s["day_note"] = True, "Asia range data incomplete - skipped"
                else:
                    tv = self.trend(day, df)
                    c = a["c"][i]
                    up, dn = (c > rh) and tv > 0, (c < rl) and tv < 0
                    if up or dn:
                        s["day_done"] = True
                        sd = 1 if up else -1
                        brk = sd * (c - (rh if up else rl)) / a["atr"][i]
                        if self.h4_sign(df, ts) != sd:
                            s["day_note"] = "breakout seen but 4h trend disagrees -> skipped (filter)"
                        elif not brk >= K_ATR:
                            s["day_note"] = f"breakout only {brk:.2f} ATR beyond range (< {K_ATR}) -> skipped (filter)"
                        else:
                            s["pending"] = dict(side=sd, entry_ts=ts + MS, rh=float(rh), rl=float(rl), brk=float(brk))
                            s["day_note"] = "signal found, waiting for entry candle"
            if m == 720 and not s["day_done"] and not s["pos"] and not s["pending"]:
                tv = self.trend(day, df)
                s["day_done"] = True
                s["day_note"] = ("daily trend flat/unknown - no trade" if tv == 0 else
                                 f"no trend-aligned ({'long' if tv > 0 else 'short'}) breakout by 12:00 UTC - no trade")
            if m >= LAST_M and s["daily_sent"] != day and a["dow"][i] < 5 and ts + MS >= s["live_from_ms"]:
                s["daily_sent"] = day
                self.events += 1
                self.emit(self.daily_msg(day))
            s["last_ts"] = ts
        # entry fills at the open of the (still forming) candle right after the signal
        if s["pending"]:
            j = np.flatnonzero(ts_a == s["pending"]["entry_ts"])
            if len(j):
                self._open(s["pending"], float(a["o"][j[0]]), int(ts_a[j[0]]))

    def daily_msg(self, day: str) -> str:
        st = stats(load_trades())
        return (f"🗓 DAILY SUMMARY | PAXG S1 paper | {day}\n"
                f"Today: {self.s['day_note'] or 'no activity'}\n\n{statement(st, 1 if self.s['pos'] else 0)}\n{DISCLAIMER}")


def fresh_state(now_ms: int) -> dict:
    return dict(live_from_ms=now_ms, last_ts=(now_ms // 86_400_000) * 86_400_000 - 1, day="", day_done=True, day_note="", pending=None, pos=None,
                daily_sent="", n_closed=0, started=utc(now_ms).strftime("%Y-%m-%d %H:%M UTC"))


def load_state(now_ms: int) -> tuple[dict, bool]:
    if os.path.exists(state_path()):
        return json.load(open(state_path())), False
    return fresh_state(now_ms), True


def save_state(s: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(s, open(state_path(), "w"), indent=1)


# ----------------------------------------------------------------------------- STATUS.md
def write_status(s: dict, df: pd.DataFrame | None) -> None:
    t, st = load_trades(), None
    st = stats(t)
    last = float(df.close.iloc[-1]) if df is not None and len(df) else None
    L = ["# PAXG S1 Paper Trader - live status",
         "",
         "> **EDUCATIONAL PURPOSE ONLY.** Paper trading, no real orders, not financial advice. "
         "Backtest and paper results do not guarantee future results.",
         "",
         f"- Updated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} | Started: {s.get('started', '?')}",
         f"- Instrument: {PAIR} 15m | Paper capital ₹{CAPITAL:,.0f} x {LEV:g}x = ₹{CAPITAL * LEV:,.0f} per trade | "
         f"USD/INR {FX:g} | fee {FEE * 100:.3f}% + slippage {SLIP * 100:.2f}% per side",
         f"- Last price: {usd(last) if last else 'n/a'} | Today ({s.get('day', '')}): {s.get('day_note') or '-'}",
         "", "## Open position"]
    p = s.get("pos")
    if p and last:
        unreal_g = p["side"] * (last - p["entry"]) * p["qty"] * FX
        cost = (FEE + SLIP) * (p["entry"] + last) * p["qty"] * FX
        L += ["| Side | Entry | Now | Stop | Target | Unrealized gross | Unrealized net | Since |", "|---|---|---|---|---|---|---|---|",
              f"| {'LONG' if p['side'] == 1 else 'SHORT'} | {usd(p['entry'])} | {usd(last)} | {usd(p['stop'])} | {usd(p['tp'])} | "
              f"{inr(unreal_g)} | {inr(unreal_g - cost)} | {fmt_t(p['entry_ts'])} |"]
    else:
        L.append("None")
    L += ["", "## Closed-trade statement", "```", statement(st, 1 if p else 0), "```", "", "## Last 20 closed trades",
          "| # | Side | Entry time | Exit time | Entry | Exit | Reason | Gross ₹ | Net ₹ |", "|---|---|---|---|---|---|---|---|---|"]
    for _, r in t.tail(20).iloc[::-1].iterrows():
        L.append(f"| {r.id} | {'L' if int(r.side) == 1 else 'S'} | {r.entry_time_utc} | {r.exit_time_utc} | {r.entry} | "
                 f"{r.exit} | {r.reason} | {inr(float(r.gross_inr))} | {inr(float(r.net_inr))} |")
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
    r = subprocess.run(["git", "push", "-q", "origin", "HEAD:paxg-paper-state"], cwd=STATE_DIR, capture_output=True, text=True)
    if r.returncode:
        subprocess.run(["git", "pull", "-q", "--rebase", "origin", "paxg-paper-state"], cwd=STATE_DIR, capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:paxg-paper-state"], cwd=STATE_DIR)


# ----------------------------------------------------------------------------- runners
def cycle(eng: Engine, df: pd.DataFrame) -> pd.DataFrame:
    now = int(time.time())
    df = merge(df, fetch(now - 3 * 86400, now))
    ev0 = eng.events
    eng.step(df, now * 1000)
    save_state(eng.s)
    write_status(eng.s, df)
    return df, eng.events > ev0


def run_loop(minutes: int) -> None:
    now = int(time.time())
    df = fetch(now - 125 * 86400, now)
    if len(df) < 5000:
        sys.exit(f"not enough candles ({len(df)}) - API problem?")
    s, first = load_state(now * 1000)
    eng = Engine(s)
    if first:
        notify(f"🚀 PAXG S1 paper trader STARTED ({PAIR}, {RES_MIN}m)\nPaper capital ₹{CAPITAL:,.0f} x {LEV:g}x = ₹{CAPITAL * LEV:,.0f} per trade.\n"
               f"Rules: Asia-range breakout 07:00-12:00 UTC, daily EMA20 + 4h trend agree, breakout >= {K_ATR} ATR, stop = opposite side of range, "
               f"target {RR}x stop, time exit 16:00 UTC.\n{DISCLAIMER}")
    end, last_commit = time.time() + minutes * 60, 0.0
    while True:
        df, changed = cycle(eng, df)
        if changed or time.time() - last_commit > 3600:
            commit_state("paper: update status/journal")
            last_commit = time.time()
        # sleep to the next 5-minute boundary + 2 min
        nxt = (int(time.time()) // SEC + 1) * SEC + 120
        if nxt > end:
            break
        time.sleep(max(1, nxt - time.time()))
    commit_state("paper: end of run")


def replay(path: str) -> None:
    df = pd.read_csv(path)
    df = df.rename(columns={"time": "ts"})[["ts", "open", "high", "low", "close", "volume"]].sort_values("ts").drop_duplicates("ts")
    df = df.reset_index(drop=True)
    rows = []
    s = fresh_state(0)
    eng = Engine(s, emit=lambda m: None, persist_trade=rows.append)
    eng.step(df, int(df.ts.iloc[-1]) + MS)
    print(f"replay: {len(rows)} closed trades")
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(os.getenv("PAXG_REPLAY_OUT", "."), "replay_trades.csv"), index=False)
    print(t[["gross_pct", "net_inr"]].astype(float).agg(["count", "mean", "sum"]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "loop":
        run_loop(int(sys.argv[2]) if len(sys.argv) > 2 else 340)
    elif cmd == "replay":
        replay(sys.argv[2])
    else:
        run_loop(0)
