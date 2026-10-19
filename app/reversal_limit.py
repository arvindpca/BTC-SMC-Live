"""Paper-only reversal and LIMIT-order lifecycle; no exchange connectivity."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Side = Literal["LONG", "SHORT"]
OrderStatus = Literal["PENDING", "FILLED", "CANCELLED"]

@dataclass
class PaperPosition:
    side: Side
    entry_price: float
    stop_loss: float
    take_profit: float

@dataclass
class PendingLimit:
    side: Side
    limit_price: float
    stop_loss: float
    take_profit: float
    bars_waited: int = 0
    status: OrderStatus = "PENDING"

@dataclass
class ReversalResult:
    closed_position: PaperPosition | None
    pending_order: PendingLimit | None
    note: str

def on_confirmed_signal(position: PaperPosition | None, signal: Side, close_price: float,
                        stop_loss: float, take_profit: float) -> ReversalResult:
    """Close an opposite paper position at signal close, then queue a paper LIMIT."""
    if close_price <= 0 or stop_loss <= 0 or take_profit <= 0:
        raise ValueError("Prices must be positive")
    if signal == "LONG" and not stop_loss < close_price < take_profit:
        raise ValueError("LONG requires SL < limit < TP")
    if signal == "SHORT" and not take_profit < close_price < stop_loss:
        raise ValueError("SHORT requires TP < limit < SL")
    if position is not None and position.side == signal:
        return ReversalResult(None, None, "IGNORED_SAME_SIDE_SIGNAL")
    order = PendingLimit(signal, close_price, stop_loss, take_profit)
    return ReversalResult(position, order,
                          "OLD_POSITION_CLOSED_LIMIT_PENDING" if position else "LIMIT_PENDING")

def fill_pending_limit(order: PendingLimit, candle_low: float, candle_high: float) -> bool:
    """Simulate fill only if a later candle range touches the limit price."""
    if order.status != "PENDING":
        return False
    if candle_low <= 0 or candle_low > candle_high:
        raise ValueError("Invalid candle range")
    if candle_low <= order.limit_price <= candle_high:
        order.status = "FILLED"
        return True
    return False

def age_pending_limit(order: PendingLimit, max_wait_bars: int) -> bool:
    """Age a pending order and cancel it at the caller-selected expiry."""
    if max_wait_bars < 1:
        raise ValueError("max_wait_bars must be >= 1")
    if order.status != "PENDING":
        return False
    order.bars_waited += 1
    if order.bars_waited >= max_wait_bars:
        order.status = "CANCELLED"
        return True
    return False
