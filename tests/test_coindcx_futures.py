import json

import pytest

from app.coindcx_futures import CoinDCXError, FuturesClient, sign_payload


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self.payload


class Session:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.posts, self.gets = payload, status, [], []

    def post(self, url, *, data, headers, timeout):
        self.posts.append((url, data, headers))
        return Resp(self.payload, self.status)

    def get(self, url, *, params=None, timeout):
        self.gets.append((url, params))
        return Resp(self.payload, self.status)


def test_signature_known_vector():
    # HMAC-SHA256(key="secret", msg="hello")
    assert sign_payload("secret", "hello") == (
        "88aab3ede8d3adf94d26ab90d3bafd4a2083070c3bcce9c014ee04a443847c0b"
    )


def test_signed_post_signs_exact_body_and_sets_headers():
    session = Session([])
    client = FuturesClient("KEY", "SECRET", session=session, clock=lambda: 1_800_000_000.0)
    client.list_positions()
    url, data, headers = session.posts[0]
    assert url.endswith("/exchange/v1/derivatives/futures/positions")
    assert headers["X-AUTH-APIKEY"] == "KEY"
    assert headers["X-AUTH-SIGNATURE"] == sign_payload("SECRET", data)
    body = json.loads(data)
    assert body["timestamp"] == 1_800_000_000_000
    assert body["margin_currency_short_name"] == ["INR"]


def test_error_message_never_contains_secret():
    client = FuturesClient("KEY", "SECRET", session=Session({"message": "bad"}, status=401))
    with pytest.raises(CoinDCXError) as err:
        client.list_positions()
    assert "SECRET" not in str(err.value) and "KEY" not in str(err.value)


def test_market_order_body_is_nested_inr_isolated_market():
    client = FuturesClient("k", "s")
    body = client.market_order_body(pair="B-BTC_USDT", side="buy", quantity=0.002, leverage=30)
    order = body["order"]
    assert order["order_type"] == "market_order"
    assert order["margin_currency_short_name"] == "INR"
    assert order["leverage"] == 30 and order["total_quantity"] == 0.002


@pytest.mark.parametrize("side,qty,lev", [("hold", 1, 1), ("buy", 0, 1), ("buy", 1, 0)])
def test_market_order_rejects_bad_input(side, qty, lev):
    with pytest.raises(ValueError):
        FuturesClient("k", "s").market_order_body(pair="B-BTC_USDT", side=side, quantity=qty, leverage=lev)


def test_open_position_ignores_flat_and_other_pairs():
    positions = [
        {"pair": "B-ETH_USDT", "active_pos": 1},
        {"pair": "B-BTC_USDT", "active_pos": 0},
        {"pair": "B-BTC_USDT", "active_pos": 0.002, "id": "p1"},
    ]
    client = FuturesClient("k", "s", session=Session(positions))
    assert client.open_position("B-BTC_USDT")["id"] == "p1"


def test_missing_credentials_rejected():
    with pytest.raises(CoinDCXError):
        FuturesClient("", "")


def test_usdt_inr_rate_sanity_band():
    ok = Session([{"market": "USDTINR", "last_price": "88.5"}])
    assert FuturesClient("k", "s", session=ok).usdt_inr_rate() == 88.5
    bad = Session([{"market": "USDTINR", "last_price": "0.5"}])
    with pytest.raises(CoinDCXError):
        FuturesClient("k", "s", session=bad).usdt_inr_rate()


class PagedSession(Session):
    """Returns full pages for page 1..N-1 and a short last page."""
    def __init__(self, pages):
        super().__init__(None)
        self.pages = pages

    def post(self, url, *, data, headers, timeout):
        page = int(json.loads(data)["page"])
        self.posts.append((url, data, headers))
        return Resp(self.pages[page - 1] if page <= len(self.pages) else [])


def test_open_position_found_on_second_page():
    flat = [{"pair": "B-BTC_USDT", "active_pos": 0, "id": f"old{i}"} for i in range(50)]
    session = PagedSession([flat, [{"pair": "B-BTC_USDT", "active_pos": 0.001, "id": "live1"}]])
    client = FuturesClient("k", "s", session=session)
    assert client.open_position("B-BTC_USDT")["id"] == "live1"
    assert len(session.posts) == 2


def test_paging_is_bounded():
    full = [{"pair": "B-ETH_USDT", "active_pos": 0, "id": str(i)} for i in range(50)]
    session = PagedSession([full] * 20)
    client = FuturesClient("k", "s", session=session)
    assert client.open_position("B-BTC_USDT") is None
    assert len(session.posts) == FuturesClient.POSITIONS_MAX_PAGES
