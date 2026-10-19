from app.strategy_smc import _bias, _swings, evaluate_signal


def candle(i, o, h, l, c):
    return {"timestamp": f"2026-01-{i:02d}T00:00:00Z", "open": o, "high": h, "low": l, "close": c}


def test_empty_data_has_no_signal():
    assert evaluate_signal([], []) is None


def test_insufficient_history_has_no_signal():
    bars = [candle(i+1, 100, 102, 98, 101) for i in range(6)]
    assert evaluate_signal(bars, bars) is None


def test_invalid_ohlc_rejected():
    bars = [candle(i+1, 100, 90, 95, 101) for i in range(10)]
    try:
        evaluate_signal(bars, bars)
    except ValueError as exc:
        assert "Invalid OHLC" in str(exc)
    else:
        raise AssertionError("invalid candle must be rejected")


def test_swing_detector_requires_unique_extreme():
    bars = [candle(i+1, 10, h, 1, 5) for i, h in enumerate([4, 5, 6, 9, 6, 5, 4, 3])]
    highs, lows = _swings(bars)
    assert (3, 9.0) in highs


def test_flat_structure_is_neutral():
    bars = [candle(i+1, 10, 12, 8, 10) for i in range(12)]
    assert _bias(bars) == "NEUTRAL"
