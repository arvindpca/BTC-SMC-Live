"""Causal, stateless SMC entry detection for LIVE use.

This is a bar-by-bar port of the user's paper strategy (BTC_SMC_Paper.py):
1H swing-structure bias -> 5m liquidity sweep -> CHOCH close -> FVG -> FVG retest
with a rejection candle.  Entry = close of the rejection candle, stop = sweep
extreme, target = 4R.

Why it is stateless: GitHub Actions runners are ephemeral.  Instead of persisting
the setup state machine, every run replays the last few hundred completed 5m
candles from a clean state.  A setup lives at most SETUP_EXPIRY (18) bars, so the
replay converges to the same decisions as a continuous run.

Causality rules (all enforced here, tested in tests/test_strategy_live.py):
  * a swing at index k is only usable from bar k + SWING_LENGTH + 1 onward;
  * a 1H candle is only usable once it has fully closed at the 5m bar's close;
  * the decision for bar i uses candles <= i only, so appending future candles
    never changes an earlier decision.

This module never touches the network or the exchange.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SWING_LENGTH = 3
SETUP_EXPIRY = 18
FVG_EXPIRY = 12
TARGET_RR = 4
CANDLE_5M_MS = 5 * 60 * 1000
CANDLE_1H_MS = 60 * 60 * 1000


@dataclass(frozen=True)
class Signal:
    side: str  # "LONG" | "SHORT"
    entry: float  # close of the rejection candle (reference price)
    stop: float
    target: float
    risk_pct: float
    bar_time: int  # open time (ms) of the 5m rejection candle
    bar_index: int
    bias: str


def _swing_indices(highs: list[float], lows: list[float], length: int = SWING_LENGTH):
    """Indices of unique-extreme swing highs/lows, identical to the paper find_swings."""
    sh: list[int] = []
    sl: list[int] = []
    n = len(highs)
    for i in range(length, n - length):
        wh = highs[i - length : i + length + 1]
        wl = lows[i - length : i + length + 1]
        if highs[i] == max(wh) and wh.count(highs[i]) == 1:
            sh.append(i)
        if lows[i] == min(wl) and wl.count(lows[i]) == 1:
            sl.append(i)
    return sh, sl


def _h1_bias_table(h1: list[dict[str, Any]]):
    """Return a function bias(m) = 1H bias using only the first m (closed) candles."""
    highs = [float(c["high"]) for c in h1]
    lows = [float(c["low"]) for c in h1]
    sh, sl = _swing_indices(highs, lows)
    cache: dict[int, str] = {}

    def bias(m: int) -> str:
        if m in cache:
            return cache[m]
        # swings inside a prefix of length m: index k valid iff k <= m - 1 - SWING_LENGTH
        limit = m - 1 - SWING_LENGTH
        hs = [highs[k] for k in sh if k <= limit]
        ls = [lows[k] for k in sl if k <= limit]
        result = "NEUTRAL"
        if len(hs) >= 2 and len(ls) >= 2:
            if hs[-1] > hs[-2] and ls[-1] > ls[-2]:
                result = "LONG"
            elif hs[-1] < hs[-2] and ls[-1] < ls[-2]:
                result = "SHORT"
        cache[m] = result
        return result

    return bias


def scan_signals(
    candles_5m: list[dict[str, Any]], candles_1h: list[dict[str, Any]]
) -> list[Signal]:
    """Every entry the strategy would have taken (see :func:`analyze` for the diagnostics)."""
    return analyze(candles_5m, candles_1h)[0]


def analyze(
    candles_5m: list[dict[str, Any]], candles_1h: list[dict[str, Any]]
) -> tuple[list[Signal], dict[str, Any]]:
    """Replay all candles; return (signals, info).  ``info`` explains what the scan saw: current
    1H bias, the setup stage at the last candle and the recent events (sweep, CHOCH, FVG, expiry...).

    Replay all candles and return every entry the strategy would have taken.

    Both inputs must contain COMPLETED candles only, with ``timestamp`` = open time
    in epoch milliseconds.  Raises ValueError on unsorted/duplicate input.
    """
    c5 = list(candles_5m)
    h1 = list(candles_1h)
    for name, rows in (("5m", c5), ("1h", h1)):
        times = [int(r["timestamp"]) for r in rows]
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError(f"{name} candles must be strictly increasing in time")
    if len(c5) < 20 or len(h1) < 8:
        return [], {"note": f"not enough candles (5m={len(c5)}, 1h={len(h1)})", "events": []}

    t5 = [int(r["timestamp"]) for r in c5]
    o5 = [float(r["open"]) for r in c5]
    hi5 = [float(r["high"]) for r in c5]
    lo5 = [float(r["low"]) for r in c5]
    cl5 = [float(r["close"]) for r in c5]
    t1 = [int(r["timestamp"]) for r in h1]

    sh_idx, sl_idx = _swing_indices(hi5, lo5)
    bias_of = _h1_bias_table(h1)

    signals: list[Signal] = []
    setup: dict[str, Any] | None = None
    events: list[tuple[int, str, str]] = []

    def ev(idx: int, name: str, detail: str = "") -> None:
        events.append((t5[idx], name, detail))

    bias = "NEUTRAL"
    m = 0  # number of closed 1H candles at the current 5m bar
    sh_ptr = sl_ptr = 0
    known_sh: list[int] = []
    known_sl: list[int] = []

    for i in range(len(c5)):
        bar_time = t5[i]
        while m < len(t1) and t1[m] + CANDLE_1H_MS <= bar_time + CANDLE_5M_MS:
            m += 1
        bias = bias_of(m)

        # swings known from df.iloc[:i]  ->  index k <= i - 1 - SWING_LENGTH
        limit = i - 1 - SWING_LENGTH
        while sh_ptr < len(sh_idx) and sh_idx[sh_ptr] <= limit:
            known_sh.append(sh_idx[sh_ptr])
            sh_ptr += 1
        while sl_ptr < len(sl_idx) and sl_idx[sl_ptr] <= limit:
            known_sl.append(sl_idx[sl_ptr])
            sl_ptr += 1

        entered_now = False

        if setup:
            if bar_time - setup["sweep_time"] > SETUP_EXPIRY * CANDLE_5M_MS:
                ev(i, "SETUP_EXPIRED", f"{setup['side']} setup older than {SETUP_EXPIRY} candles (was at {setup['stage']})")
                setup = None
            elif setup["side"] != bias:
                ev(i, "BIAS_CHANGED", f"1H bias is now {bias}; dropped {setup['side']} setup (was at {setup['stage']})")
                setup = None

        if setup:
            is_long = setup["side"] == "LONG"
            invalidated = lo5[i] < setup["extreme"] if is_long else hi5[i] > setup["extreme"]
            if invalidated:
                ev(i, "SETUP_INVALIDATED", f"price broke the sweep extreme {setup['extreme']:.1f} (was at {setup['stage']})")
                setup = None
            elif setup["stage"] == "SWEEP":
                level = setup["break_level"]
                if level is not None:
                    choch = cl5[i] > level if is_long else cl5[i] < level
                    if choch:
                        setup["stage"] = "CHOCH"
                        ev(i, "CHOCH", f"{setup['side']} close {cl5[i]:.1f} broke {level:.1f}")
            elif setup["stage"] == "CHOCH":
                if i >= 2:
                    first_high, first_low = hi5[i - 2], lo5[i - 2]
                    if is_long and lo5[i] > first_high:
                        setup.update(stage="FVG", fvg_low=first_high, fvg_high=lo5[i], fvg_time=bar_time)
                        ev(i, "FVG", f"zone {first_high:.1f} - {lo5[i]:.1f}")
                    elif (not is_long) and hi5[i] < first_low:
                        setup.update(stage="FVG", fvg_low=hi5[i], fvg_high=first_low, fvg_time=bar_time)
                        ev(i, "FVG", f"zone {hi5[i]:.1f} - {first_low:.1f}")
            elif setup["stage"] == "FVG":
                if bar_time - setup["fvg_time"] > FVG_EXPIRY * CANDLE_5M_MS:
                    ev(i, "FVG_EXPIRED", f"no retest within {FVG_EXPIRY} candles")
                    setup = None
                else:
                    touched = lo5[i] <= setup["fvg_high"] and hi5[i] >= setup["fvg_low"]
                    if is_long:
                        rejection = cl5[i] > o5[i] and cl5[i] > setup["fvg_high"]
                    else:
                        rejection = cl5[i] < o5[i] and cl5[i] < setup["fvg_low"]
                    if touched and rejection:
                        entry = cl5[i]
                        stop = setup["extreme"]
                        risk = abs(entry - stop)
                        risk_pct = risk / entry * 100
                        valid_direction = stop < entry if is_long else stop > entry
                        # No min/max risk-% filter (removed on request); only the stop must be on the correct side.
                        if valid_direction:
                            target = entry + TARGET_RR * risk if is_long else entry - TARGET_RR * risk
                            signals.append(
                                Signal(
                                    side=setup["side"],
                                    entry=entry,
                                    stop=stop,
                                    target=target,
                                    risk_pct=risk_pct,
                                    bar_time=bar_time,
                                    bar_index=i,
                                    bias=bias,
                                )
                            )
                            entered_now = True
                            ev(i, "ENTRY_SIGNAL", f"{setup['side']} entry {entry:.1f} SL {stop:.1f} TP {target:.1f}")
                        else:
                            ev(i, "ENTRY_SKIPPED", "stop on the wrong side of entry")
                        setup = None

        # A new sweep may only start when no position would be open on this bar
        # (the paper bot also skips sweep detection on the bar it opens a trade).
        if setup is None and bias in ("LONG", "SHORT") and not entered_now:
            if bias == "LONG" and known_sl:
                swing_low = lo5[known_sl[-1]]
                if lo5[i] < swing_low and cl5[i] > swing_low:
                    setup = {
                        "side": "LONG",
                        "stage": "SWEEP",
                        "sweep_time": bar_time,
                        "extreme": lo5[i],
                        "break_level": hi5[known_sh[-1]] if known_sh else None,
                    }
                    ev(i, "SWEEP", f"LONG: low {lo5[i]:.1f} swept swing low {swing_low:.1f}; CHOCH level " + ("-" if setup["break_level"] is None else f"{setup['break_level']:.1f}"))
            elif bias == "SHORT" and known_sh:
                swing_high = hi5[known_sh[-1]]
                if hi5[i] > swing_high and cl5[i] < swing_high:
                    setup = {
                        "side": "SHORT",
                        "stage": "SWEEP",
                        "sweep_time": bar_time,
                        "extreme": hi5[i],
                        "break_level": lo5[known_sl[-1]] if known_sl else None,
                    }
                    ev(i, "SWEEP", f"SHORT: high {hi5[i]:.1f} swept swing high {swing_high:.1f}; CHOCH level " + ("-" if setup["break_level"] is None else f"{setup['break_level']:.1f}"))

    last = len(c5) - 1
    info = {
        "last_bar_time": t5[last],
        "last_close": cl5[last],
        "bias": bias,
        "h1_closed": m,
        "last_h1_time": t1[m - 1] if m else None,
        "swing_high": hi5[known_sh[-1]] if known_sh else None,
        "swing_low": lo5[known_sl[-1]] if known_sl else None,
        "setup": None if setup is None else {**setup, "age_bars": (t5[last] - setup["sweep_time"]) // CANDLE_5M_MS},
        "signals_in_window": len(signals),
        "events": events[-12:],
        "event_counts": {k: sum(1 for e in events if e[1] == k) for k in sorted({e[1] for e in events})},
        "candles_5m": len(c5),
        "first_bar_time": t5[0],
    }
    return signals, info


def _ist(ms: int | None) -> str:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    return "-" if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(
        ZoneInfo("Asia/Kolkata")
    ).strftime("%Y-%m-%d %H:%M IST")


def _current_ist() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")


def format_scan_report(info: dict[str, Any]) -> str:
    """Human-readable scan report for the Actions log (what stage the strategy is at)."""
    if "note" in info:
        return f"SCAN REPORT: {info['note']}"
    lines = [
        "================ SMC SCAN REPORT ================",
        f"Scan time       : {_current_ist()}",
        f"Candles scanned : {info['candles_5m']} x 5m (from {_ist(info['first_bar_time'])})",
        f"Last 5m candle  : {_ist(info['last_bar_time'])}  close {info['last_close']:.1f}",
        f"1H candles used : {info['h1_closed']} closed (last {_ist(info['last_h1_time'])})",
        f"1H bias         : {info['bias']}",
        "Last swing high : " + ("-" if info["swing_high"] is None else f"{info['swing_high']:.1f}")
        + "   Last swing low: " + ("-" if info["swing_low"] is None else f"{info['swing_low']:.1f}"),
    ]
    setup = info["setup"]
    if setup is None:
        lines.append("Setup now       : NONE - waiting for a liquidity sweep in the direction of the 1H bias"
                     if info["bias"] != "NEUTRAL" else "Setup now       : NONE - 1H bias is NEUTRAL, so no sweeps are taken")
    else:
        stage_hint = {
            "SWEEP": "sweep seen; waiting for CHOCH close beyond " + ("-" if setup.get("break_level") is None else f"{setup['break_level']:.1f}"),
            "CHOCH": "CHOCH done; waiting for a fair value gap",
            "FVG": f"FVG {setup.get('fvg_low'):.1f}-{setup.get('fvg_high'):.1f} formed; waiting for retest + rejection candle",
        }.get(setup["stage"], "")
        lines.append(f"Setup now       : {setup['side']} at stage {setup['stage']} (age {setup['age_bars']} candles) - {stage_hint}")
    lines.append(f"Entry signals in scanned window: {info['signals_in_window']}")
    if info["event_counts"]:
        lines.append("Event counts    : " + ", ".join(f"{k}={v}" for k, v in info["event_counts"].items()))
    lines.append("Recent events (oldest -> newest):")
    for t, name, detail in info["events"] or [(0, "-", "no events in window")]:
        lines.append(f"  {_ist(t) if t else '-':<20} {name:<18} {detail}")
    lines.append("=================================================")
    return "\n".join(lines)


def latest_signal(
    candles_5m: list[dict[str, Any]],
    candles_1h: list[dict[str, Any]],
    max_age_bars: int = 1,
) -> Signal | None:
    """Most recent entry whose rejection candle is among the last ``max_age_bars``+1
    completed 5m candles (0 = only the newest candle).  Older signals are stale."""
    if max_age_bars < 0:
        raise ValueError("max_age_bars must be >= 0")
    signals = scan_signals(candles_5m, candles_1h)
    if not signals:
        return None
    last = signals[-1]
    if last.bar_index >= len(candles_5m) - 1 - max_age_bars:
        return last
    return None
