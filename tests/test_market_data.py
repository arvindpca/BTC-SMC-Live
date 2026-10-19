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


# Use realistic Unix timestamps so millisecond normalization matches production.
BASE = 1_800_000_000


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
        fetch_latest_candles(now_seconds=BASE, session=FakeSession({"s": "ok", "data": []}))


def test_fetches_completed_candles_sorted_and_excludes_open_candle():
    # BASE is aligned to a 5-minute boundary; the candle opening at BASE is incomplete.
    session = FakeSession({
        "s": "ok",
        "data": [candle(BASE), candle(BASE - 300), candle(BASE - 600)],
    })
    rows = fetch_latest_candles(
        pair="B-BTC_USDT", limit=10, now_seconds=BASE, session=session
    )
    assert [row["timestamp"] for row in rows] == [
        (BASE - 600) * 1000,
        (BASE - 300) * 1000,
    ]
    assert session.calls[0][1]["resolution"] == "5"
    assert session.calls[0][1]["pcode"] == "f"


def test_rejects_unexpected_api_payload():
    with pytest.raises(MarketDataError):
        fetch_latest_candles(
            pair="B-BTC_USDT", now_seconds=BASE, session=FakeSession({"s": "error"})
        )


def test_rejects_bad_pair_format():
    with pytest.raises(MarketDataNotConfigured):
        fetch_latest_candles(
            pair="BTCINR", now_seconds=BASE, session=FakeSession({"s": "ok", "data": []})
        )


def test_discards_invalid_ohlcv_and_duplicate_timestamp_keeps_latest():
    bad = {**candle(BASE - 300), "high": 1}
    earlier = candle(BASE - 600, price=100)
    latest_duplicate = candle(BASE - 600, price=110)
    session = FakeSession({"s": "ok", "data": [bad, earlier, latest_duplicate]})
    rows = fetch_latest_candles(
        pair="B-BTC_USDT", limit=10, now_seconds=BASE, session=session
    )
    assert len(rows) == 1
    assert rows[0]["open"] == 110


def test_limit_must_be_in_range():
    with pytest.raises(ValueError):
        fetch_latest_candles(
            pair="B-BTC_USDT", limit=1, now_seconds=BASE,
            session=FakeSession({"s": "ok", "data": []}),
        )
