"""Fail-closed risk guardrails for paper-mode research."""
from __future__ import annotations


def allow_new_position(open_positions: int, max_positions: int = 1) -> bool:
    if open_positions < 0 or max_positions < 1:
        return False
    return open_positions < max_positions


def validate_risk_parameters(sl_pct: float, tp_pct: float) -> None:
    if not (0 < sl_pct < 100 and 0 < tp_pct < 100):
        raise ValueError("SL and TP percentages must be between 0 and 100")
