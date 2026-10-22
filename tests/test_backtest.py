from types import SimpleNamespace

from app.backtest import simulate


def candles(*hl):
    return [{"timestamp": i * 300000, "open": 100, "high": h, "low": l, "close": 100} for i, (h, l) in enumerate(hl)]


def sig(side="LONG"):
    if side == "LONG":
        return SimpleNamespace(side="LONG", entry=100.0, stop=99.0, target=104.0, bar_index=0)
    return SimpleNamespace(side="SHORT", entry=100.0, stop=101.0, target=96.0, bar_index=0)


def test_stop_loss_is_minus_one_r():
    r, _, how = simulate(sig(), candles((100, 100), (100.5, 98.9)))
    assert (r, how) == (-1.0, "SL")


def test_target_is_four_r():
    r, _, how = simulate(sig(), candles((100, 100), (104.2, 99.5)))
    assert (r, how) == (4.0, "TARGET_4R")


def test_trail_to_2r_then_stopped_at_plus_two():
    r, _, how = simulate(sig(), candles((100, 100), (102.1, 99.5), (101, 101.9)))
    assert (r, how) == (2.0, "TRAIL_2R")


def test_milestone_applies_from_next_candle():
    # candle 1 reaches 2R (stop stays 99 inside it); candle 2 trades back to 101 -> stopped at the new 2R stop (102)
    r, _, how = simulate(sig(), candles((100, 100), (102.5, 100.5), (101, 98.9)))
    assert (r, how) == (2.0, "TRAIL_2R")


def test_short_target():
    r, _, how = simulate(sig("SHORT"), candles((100, 100), (100.5, 95.9)))
    assert (r, how) == (4.0, "TARGET_4R")
