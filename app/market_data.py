"""Read-only CoinDCX futures candles for PAPER/research use only.

This module never authenticates, places orders, or changes account state.
Set COINDCX_FUTURES_PAIR to an exact pair verified against CoinDCX's active
INR-margin futures instruments (for example, do not assume a spot ticker works).
Official endpoint: https://public.coindcx.com/market_data/candlesticks
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

BASE_URL = "https://public.coindcx.com/market_data/candlesticks"
INTERVAL_SECONDS = 300
DEFAULT_LIMIT = 100
TIMEOUT_SECONDS = 10


class MarketDataNotConfigured(RuntimeError):
    """Raised when a verified futures instrument has not been configured."""


class MarketDataError(RuntimeError):
    """Raised when CoinDCX returns invalid or unavailable candle data."""


def fetch_latest_candles(
    pair: str | None = None,
    limit: int = DEFAULT_LIMIT,
    *,
    now_seconds: int | None = None,
    session: Any = requests,
) -> list[dict[str, float | int]]:
    """Fetch completed 5-minute futures candles in ascending time order.

    Returns dictionaries with timestamp (UTC epoch milliseconds), open, high,
    low, close and volume. The currently forming candle is always excluded.
    """
    instrument = (pair or os.getenv("COINDCX_FUTURES_PAIR", "")).strip()
    if not instrument:
        raise MarketDataNotConfigured(
            "Set COINDCX_FUTURES_PAIR to the exact verified CoinDCX INR-margin "
            "futures instrument pair before requesting candles."
        )
    if not instrument.startswith("B-") or "_" not in instrument:
        raise MarketDataNotConfigured(
            "COINDCX_FUTURES_PAIR must be an exact CoinDCX futures pair such as "
            "the format returned by the official active-instruments endpoint."
        )
    if not isinstance(limit, int) or isinstance(limit, bool) or not 2 <= limit <= 500:
        raise ValueError("limit must be an integer from 2 to 500")

    now = int(time.time()) if now_seconds is None else int(now_seconds)
    params = {
        "pair": instrument,
        "from": max(0, now - (limit + 2) * INTERVAL_SECONDS),
        "to": now,
        "resolution": "5",
        "pcode": "f",
    }
    try:
        response = session.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MarketDataError(f"CoinDCX candle request failed: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("s") != "ok":
        raise MarketDataError("CoinDCX returned an unexpected futures-candle response.")
    raw = payload.get("data")
    if not isinstance(raw, list):
        raise MarketDataError("CoinDCX candle response has no data list.")

    candles: dict[int, dict[str, float | int]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            # CoinDCX documents candle time as epoch milliseconds.
            raw_time = int(float(item["time"]))
            timestamp_ms = raw_time if raw_time > 10**12 else raw_time * 1000
            open_ = float(item["open"])
            high = float(item["high"])
            low = float(item["low"])
            close = float(item["close"])
            volume = float(item["volume"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

        # Reject malformed, non-finite, or impossible OHLCV records.
        values = (open_, high, low, close, volume)
        if not all(map(__import__("math").isfinite, values)) or min(open_, high, low, close) <= 0 or volume < 0:
            continue
        if high < max(open_, low, close) or low > min(open_, high, close):
            continue
        # A candle is usable only after its full 5-minute interval has closed.
        if timestamp_ms + INTERVAL_SECONDS * 1000 > now * 1000:
            continue
        candles[timestamp_ms] = {
            "timestamp": timestamp_ms,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    result = [candles[key] for key in sorted(candles)]
    if not result:
        raise MarketDataError("No valid completed 5-minute futures candles returned.")
    return result[-limit:]
