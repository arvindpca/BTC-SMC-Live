"""Minimal virtual broker. This module deliberately has no exchange order API."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class VirtualOrder:
    side: Literal["BUY", "SELL"]
    quantity: float
    reference_price: float
    stop_loss: float
    take_profit: float


def validate_virtual_order(order: VirtualOrder) -> None:
    if order.side not in {"BUY", "SELL"}:
        raise ValueError("Unsupported side")
    values = (order.quantity, order.reference_price, order.stop_loss, order.take_profit)
    if not all(v > 0 for v in values):
        raise ValueError("Quantity, price, SL and TP must be positive")
    if order.side == "BUY" and not (order.stop_loss < order.reference_price < order.take_profit):
        raise ValueError("BUY requires SL < reference price < TP")
    if order.side == "SELL" and not (order.take_profit < order.reference_price < order.stop_loss):
        raise ValueError("SELL requires TP < reference price < SL")
