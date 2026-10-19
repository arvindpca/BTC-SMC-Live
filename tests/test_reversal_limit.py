import pytest
from app.reversal_limit import PaperPosition, age_pending_limit, fill_pending_limit, on_confirmed_signal

def test_long_to_short_closes_then_queues_limit():
    old = PaperPosition("LONG", 100, 95, 120)
    result = on_confirmed_signal(old, "SHORT", 110, 115, 90)
    assert result.closed_position is old
    assert result.pending_order.side == "SHORT"
    assert result.pending_order.limit_price == 110
    assert result.pending_order.status == "PENDING"

def test_short_to_long_closes_then_queues_limit():
    old = PaperPosition("SHORT", 100, 105, 80)
    result = on_confirmed_signal(old, "LONG", 90, 85, 110)
    assert result.closed_position is old
    assert result.pending_order.side == "LONG"

def test_no_position_creates_pending_not_open_position():
    result = on_confirmed_signal(None, "LONG", 100, 95, 120)
    assert result.closed_position is None
    assert result.pending_order.status == "PENDING"

def test_same_side_signal_does_not_duplicate():
    old = PaperPosition("LONG", 100, 95, 120)
    result = on_confirmed_signal(old, "LONG", 105, 95, 125)
    assert result.pending_order is None
    assert result.note == "IGNORED_SAME_SIDE_SIGNAL"

def test_limit_fills_only_when_candle_touches_price():
    order = on_confirmed_signal(None, "LONG", 100, 95, 120).pending_order
    assert not fill_pending_limit(order, 101, 103)
    assert order.status == "PENDING"
    assert fill_pending_limit(order, 99, 102)
    assert order.status == "FILLED"

def test_expiry_cancels_pending_limit():
    order = on_confirmed_signal(None, "SHORT", 100, 105, 80).pending_order
    assert not age_pending_limit(order, 2)
    assert age_pending_limit(order, 2)
    assert order.status == "CANCELLED"

def test_invalid_long_prices_rejected():
    with pytest.raises(ValueError):
        on_confirmed_signal(None, "LONG", 100, 105, 120)
