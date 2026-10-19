"""Public market-data placeholder.

Before use, confirm the official CoinDCX endpoint, product type, quote currency,
instrument identifier, candle semantics, rate limits, and timestamp timezone.
This module intentionally does not guess an INR futures ticker or endpoint.
"""
from __future__ import annotations


class MarketDataNotConfigured(RuntimeError):
    pass


def fetch_latest_candles():
    raise MarketDataNotConfigured(
        "Market data adapter is not configured: verify the CoinDCX INR futures "
        "instrument and official API contract before enabling data polling."
    )
