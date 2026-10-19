from __future__ import annotations

import pytest

from app.market_data import (
    MarketDataError,
    MarketDataNotConfigured,
    fetch_latest_candles,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        return FakeResponse(self.payload)


def candle(open_time_seconds, price=100):
    return {
        "time": open_time_seconds * 1000,
        "open": price,
        "high": price + 2,
        "low": price - 2,
        "close": price + 1,
        "volume": 3,
    }


def test_requires_explicit_verified_pair(monkeypatch):
    monkeypatch.delenv("COINDCX_FUTURES_PAIR", raising=False)
    with pytest.raises(MarketDataNotConfigured):
        fetch_latest_candles(now_seconds=1800, session=FakeSession({"s": "ok", "data": []}))


def test_fetches_completed_candles_sorted_and_excludes_open_candle():
    session = FakeSession({"s": "ok", "data": [candle(1800), candle(1200), candle(1500)]})
    rows = fetch_latest_candles(
        pair="B-BTC_USDT", limit=10, now_seconds=1800, session=session
    )
    assert [row["timestamp"] for row in rows] == [1200_000, 1500_000]
    assert session.calls[0][1]["resolution"] == "5"
    assert session.calls[0][1]["pcode"] == "f"


def test_rejects_unexpected_api_payload():
    with pytest.raises(MarketDataError):
        fetch_latest_candles(
            pair="B-BTC_USDT", now_seconds=1800, session=FakeSession({"s": "error"})
        )


def test_rejects_bad_pair_format():
    with pytest.raises(MarketDataNotConfigured):
        fetch_latest_candles(
            pair="BTCINR", now_seconds=1800, session=FakeSession({"s": "ok", "data": []})
        )
