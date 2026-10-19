"""Deterministic SMC signal logic derived from BTC_SMC_Paper.py.

Input candles are dictionaries with timestamp, open, high, low, close. Timestamps
must be consistently sortable ISO-8601 strings. This module only creates signals;
it never places orders.
"""
from __future__ import annotations

from typing import Any


def _valid_candle(c: dict[str, Any]) -> bool:
    try:
        o, h, l, cl = (float(c[k]) for k in ("open", "high", "low", "close"))
        return l > 0 and h >= max(o, cl, l) and l <= min(o, cl) and all(
            v == v and abs(v) != float("inf") for v in (o, h, l, cl)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _swings(candles: list[dict[str, Any]], length: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(length, len(candles) - length):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        win_h = [float(candles[j]["high"]) for j in range(i-length, i+length+1)]
        win_l = [float(candles[j]["low"]) for j in range(i-length, i+length+1)]
        if h == max(win_h) and win_h.count(h) == 1:
            highs.append((i, h))
        if l == min(win_l) and win_l.count(l) == 1:
            lows.append((i, l))
    return highs, lows


def _bias(candles: list[dict[str, Any]]) -> str:
    highs, lows = _swings(candles)
    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"
    hh = highs[-1][1] > highs[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    if hh and hl:
        return "LONG"
    if lh and ll:
        return "SHORT"
    return "NEUTRAL"


def evaluate_signal(candles_5m: list[dict[str, Any]], candles_1h: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return only a completed-bar signal; None when no complete setup exists.

    Signal fields: side, entry_reference (confirmation close), stop_loss,
    take_profit (4R), risk_pct, timestamp, setup. H1 candles must already be closed.
    """
    if not candles_5m or not candles_1h:
        return None
    if any(not _valid_candle(c) for c in candles_5m + candles_1h):
        raise ValueError("Invalid OHLC candle")
    c5 = sorted(candles_5m, key=lambda c: c["timestamp"])
    h1 = sorted(candles_1h, key=lambda c: c["timestamp"])
    if len(c5) < 8 or len(h1) < 8:
        return None
    # Do not evaluate a still-forming last candle; caller must pass completed bars only.
    swings_h, swings_l = _swings(c5)
    if not swings_h or not swings_l:
        return None
    latest_sweep: dict[str, Any] | None = None
    for i, bar in enumerate(c5):
        # Only use H1 bars whose timestamp is at or before this 5m bar.
        h1_closed = [h for h in h1 if h["timestamp"] <= bar["timestamp"]]
        bias = _bias(h1_closed) if len(h1_closed) >= 7 else "NEUTRAL"
        if bias == "NEUTRAL":
            continue
        prior_highs = [(idx, val) for idx, val in swings_h if idx < i]
        prior_lows = [(idx, val) for idx, val in swings_l if idx < i]
        sh = prior_highs[-1][1] if prior_highs else None
        sl = prior_lows[-1][1] if prior_lows else None
        if bias == "LONG" and sl is not None and float(bar["low"]) < sl < float(bar["close"]):
            latest_sweep = {"side": "LONG", "idx": i, "extreme": float(bar["low"]), "break_level": sh}
        elif bias == "SHORT" and sh is not None and float(bar["high"]) > sh > float(bar["close"]):
            latest_sweep = {"side": "SHORT", "idx": i, "extreme": float(bar["high"]), "break_level": sl}
        if latest_sweep is None:
            continue
        age = i - latest_sweep["idx"]
        if age > 18 or (latest_sweep["side"] != bias):
            latest_sweep = None
            continue
        if i <= latest_sweep["idx"] or latest_sweep["break_level"] is None:
            continue
        close = float(bar["close"])
        side = latest_sweep["side"]
        choch = close > latest_sweep["break_level"] if side == "LONG" else close < latest_sweep["break_level"]
        if not choch:
            # Check the FVG created by the current candle and two candles earlier.
            continue
        if i < 2:
            continue
        first, current = c5[i-2], bar
        if side == "LONG" and float(current["low"]) > float(first["high"]):
            zone_low, zone_high = float(first["high"]), float(current["low"])
        elif side == "SHORT" and float(current["high"]) < float(first["low"]):
            zone_low, zone_high = float(current["high"]), float(first["low"])
        else:
            continue
        # A confirmed retest must occur after the FVG candle; never fill on its creation candle.
        for j in range(i + 1, min(len(c5), i + 13)):
            retest = c5[j]
            lo, hi = float(retest["low"]), float(retest["high"])
            overlap = lo <= zone_high and hi >= zone_low
            confirm = (float(retest["close"]) > float(retest["open"]) and float(retest["close"]) > zone_high) if side == "LONG" else (float(retest["close"]) < float(retest["open"]) and float(retest["close"]) < zone_low)
            if not overlap or not confirm:
                continue
            entry = float(retest["close"])
            stop = float(latest_sweep["extreme"])
            risk_pct = abs(entry - stop) / entry * 100.0
            if not (0.05 <= risk_pct <= 2.50) or (side == "LONG" and stop >= entry) or (side == "SHORT" and stop <= entry):
                continue
            risk = abs(entry - stop)
            target = entry + 4 * risk if side == "LONG" else entry - 4 * risk
            return {"side": side, "entry_reference": entry, "stop_loss": stop,
                    "take_profit": target, "risk_pct": risk_pct,
                    "timestamp": retest["timestamp"], "setup": "H1_BIAS_SWEEP_CHOCH_FVG_RETEST"}
    return None
