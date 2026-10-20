import random

import pytest

from app.strategy_live import latest_signal, scan_signals

T0 = 1_800_000_000_000 - (1_800_000_000_000 % 3_600_000)


def gen_5m(seed, n=1500):
    rnd = random.Random(seed)
    price, rows, drift = 100_000.0, [], 0.0
    for k in range(n):
        if k % 150 == 0:
            drift = rnd.uniform(-0.0004, 0.0004)
        o = price
        c = o * (1 + drift + rnd.gauss(0, 0.0018))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.0008)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.0008)))
        rows.append({"timestamp": T0 + k * 300_000, "open": o, "high": h, "low": l, "close": c})
        price = c
    return rows


def to_1h(rows):
    out = []
    for k in range(0, len(rows) - 11, 12):
        ch = rows[k : k + 12]
        out.append({
            "timestamp": ch[0]["timestamp"], "open": ch[0]["open"],
            "high": max(r["high"] for r in ch), "low": min(r["low"] for r in ch),
            "close": ch[-1]["close"],
        })
    return out


C5 = gen_5m(14)
H1 = to_1h(C5)
FULL = scan_signals(C5, H1)


def test_known_seed_produces_signals():
    assert len(FULL) >= 5


def test_signal_geometry():
    for s in FULL:
        risk = abs(s.entry - s.stop)
        assert s.risk_pct > 0  # no min/max risk filter any more
        if s.side == "LONG":
            assert s.stop < s.entry < s.target
            assert s.target == pytest.approx(s.entry + 4 * risk)
        else:
            assert s.target < s.entry < s.stop
            assert s.target == pytest.approx(s.entry - 4 * risk)
        assert s.bar_time == C5[s.bar_index]["timestamp"]


@pytest.mark.parametrize("cut", [300, 520, 800, 1100, 1400])
def test_future_candles_never_change_earlier_decisions(cut):
    """Prefix replay (with the FULL 1H list available) must equal the full replay up to the cut."""
    prefix = scan_signals(C5[:cut], H1)
    expected = [s for s in FULL if s.bar_index < cut]
    assert prefix == expected


def test_unclosed_future_hourly_candles_are_ignored():
    """Dropping 1H candles that had not closed yet at the cut must change nothing."""
    cut = 900
    closed_h1 = [h for h in H1 if h["timestamp"] + 3_600_000 <= C5[cut - 1]["timestamp"] + 300_000]
    assert scan_signals(C5[:cut], closed_h1) == scan_signals(C5[:cut], H1)


def test_latest_signal_freshness_window():
    s = FULL[1]
    idx = s.bar_index
    assert latest_signal(C5[: idx + 1], H1, max_age_bars=0) == s
    assert latest_signal(C5[: idx + 2], H1, max_age_bars=1) == s
    assert latest_signal(C5[: idx + 3], H1, max_age_bars=1) is None  # now two bars old
    assert latest_signal(C5[: idx + 3], H1, max_age_bars=2) == s


def test_rejects_unsorted_or_duplicate_candles():
    bad = [C5[1], C5[0]] + C5[2:30]
    with pytest.raises(ValueError):
        scan_signals(bad, H1)
    dup = C5[:30] + [C5[29]]
    with pytest.raises(ValueError):
        scan_signals(dup, H1)


def test_too_little_data_returns_nothing():
    assert scan_signals(C5[:10], H1) == []
    assert scan_signals(C5, H1[:3]) == []


# ---------------- scan report (diagnostics) ----------------
from app.strategy_live import analyze, format_scan_report  # noqa: E402


def test_analyze_signals_identical_to_scan_signals():
    signals, info = analyze(C5, H1)
    assert signals == FULL
    assert info["event_counts"].get("ENTRY_SIGNAL", 0) == len(FULL)


def test_event_counts_follow_the_strategy_funnel():
    _, info = analyze(C5, H1)
    c = info["event_counts"]
    assert c.get("SWEEP", 0) >= c.get("CHOCH", 0) >= c.get("FVG", 0) >= c.get("ENTRY_SIGNAL", 0) > 0


def test_report_shows_bias_stage_and_events():
    report = format_scan_report(analyze(C5, H1)[1])
    for text in ("SMC SCAN REPORT", "1H bias", "Setup now", "Recent events", "SWEEP"):
        assert text in report


def test_report_for_every_prefix_never_crashes_and_names_a_stage():
    for cut in (25, 80, 200, 450, 900, 1300):
        info = analyze(C5[:cut], H1)[1]
        text = format_scan_report(info)
        assert "Setup now" in text or "SCAN REPORT" in text


def test_report_with_too_little_data():
    assert "not enough candles" in format_scan_report(analyze(C5[:5], H1)[1])
