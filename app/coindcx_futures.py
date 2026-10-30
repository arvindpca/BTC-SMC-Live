"""Minimal CoinDCX Futures REST client (signed requests, no third-party SDK).

Endpoints follow the official CoinDCX Futures API reference.  Fields that the
public 2024 PDF does not document (INR margin selection) are marked UNVERIFIED
and are the reason the live runner has a DRY_RUN stage: run DRY_RUN first, check
the logged payloads/positions, and use the smallest possible size for the very
first LIVE order.

Secrets are never logged or included in exception messages.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import json
import time
from typing import Any

import requests

BASE_URL = "https://api.coindcx.com"
FUTURES_PREFIX = "/exchange/v1/derivatives/futures"
TIMEOUT_SECONDS = 15


class CoinDCXError(RuntimeError):
    """Any API/transport problem.  Message never contains credentials."""


def sign_payload(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


class FuturesClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        margin_currency: str = "INR",
        session: Any = requests,
        base_url: str = BASE_URL,
        clock=time.time,
    ) -> None:
        if not api_key or not api_secret:
            raise CoinDCXError("API key/secret missing")
        self._key = api_key
        self._secret = api_secret
        self.margin_currency = margin_currency
        proxy = os.getenv("COINDCX_PROXY", "").strip()
        if session is requests and proxy:
            # CoinDCX key fixed IP se bandhi hai -> Oracle server (proxy) ke raaste jao
            session = requests.Session()
            session.proxies = {"https": proxy, "http": proxy}
        self._session = session
        self._base = base_url.rstrip("/")
        self._clock = clock

    # ---- transport -------------------------------------------------------
    def _decode(self, response: Any) -> Any:
        if response.status_code >= 400:
            raise CoinDCXError(f"HTTP {response.status_code}: {str(response.text)[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise CoinDCXError("Response was not JSON") from exc

    def _public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._session.get(self._base + path, params=params, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise CoinDCXError(f"Request failed: {type(exc).__name__}") from exc
        return self._decode(response)

    def _signed_post(self, path: str, body: dict[str, Any]) -> Any:
        body = {**body, "timestamp": int(self._clock() * 1000)}
        payload = json.dumps(body, separators=(",", ":"))
        headers = {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": self._key,
            "X-AUTH-SIGNATURE": sign_payload(self._secret, payload),
        }
        try:
            response = self._session.post(
                self._base + path, data=payload, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise CoinDCXError(f"Request failed: {type(exc).__name__}") from exc
        return self._decode(response)

    # ---- public market info ---------------------------------------------
    def active_instruments(self) -> list[str]:
        data = self._public_get(f"{FUTURES_PREFIX}/data/active_instruments")
        if not isinstance(data, list):
            raise CoinDCXError("Unexpected active_instruments response")
        return [str(x) for x in data]

    def instrument_details(self, pair: str) -> dict[str, Any]:
        data = self._public_get(f"{FUTURES_PREFIX}/data/instrument", {"pair": pair})
        details = data.get("instrument") if isinstance(data, dict) else None
        if not isinstance(details, dict):
            raise CoinDCXError("Unexpected instrument response")
        return details

    def usdt_inr_rate(self) -> float:
        """Spot USDT/INR last price, used to size INR margin on *_USDT contracts."""
        data = self._public_get("/exchange/ticker")
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict) and row.get("market") == "USDTINR":
                    rate = float(row.get("last_price", 0))
                    if 50 < rate < 200:  # sanity band
                        return rate
        raise CoinDCXError("Could not read a sane USDTINR rate")

    # ---- account ---------------------------------------------------------
    POSITIONS_PAGE_SIZE = 50
    POSITIONS_MAX_PAGES = 6

    def _positions_page(self, page: int) -> list[dict[str, Any]]:
        # UNVERIFIED-but-working (diagnose passed): margin_currency_short_name filter for INR positions.
        body = {"page": str(page), "size": str(self.POSITIONS_PAGE_SIZE),
                "margin_currency_short_name": [self.margin_currency]}
        data = self._signed_post(f"{FUTURES_PREFIX}/positions", body)
        if isinstance(data, dict):
            data = data.get("positions", data.get("data"))
        if not isinstance(data, list):
            raise CoinDCXError("Unexpected positions response")
        return [p for p in data if isinstance(p, dict)]

    def list_positions(self) -> list[dict[str, Any]]:
        """All position rows, following pages until a short page (bounded)."""
        rows: list[dict[str, Any]] = []
        for page in range(1, self.POSITIONS_MAX_PAGES + 1):
            batch = self._positions_page(page)
            rows.extend(batch)
            if len(batch) < self.POSITIONS_PAGE_SIZE:
                break
        return rows

    def open_position(self, pair: str) -> dict[str, Any] | None:
        for position in self.list_positions():
            if position.get("pair") == pair and float(position.get("active_pos") or 0) != 0:
                return position
        return None

    # ---- trading ---------------------------------------------------------
    def create_market_order(self, *, pair: str, side: str, quantity: float, leverage: int) -> Any:
        return self._signed_post(f"{FUTURES_PREFIX}/orders/create", self.market_order_body(
            pair=pair, side=side, quantity=quantity, leverage=leverage))

    def market_order_body(self, *, pair: str, side: str, quantity: float, leverage: int) -> dict[str, Any]:
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if quantity <= 0 or leverage < 1:
            raise ValueError("quantity and leverage must be positive")
        return {
            "order": {
                "side": side,
                "pair": pair,
                "order_type": "market_order",
                "total_quantity": quantity,
                "leverage": leverage,
                "notification": "no_notification",
                "time_in_force": "good_till_cancel",
                "hidden": False,
                "post_only": False,
                # UNVERIFIED fields below: INR collateral selection.
                "margin_currency_short_name": self.margin_currency,
                "position_margin_type": "isolated",
            }
        }

    def set_tpsl(self, *, position_id: str, stop_price: float, target_price: float) -> Any:
        body = {
            "id": position_id,
            "take_profit": {
                "stop_price": target_price,
                "limit_price": target_price,
                "order_type": "take_profit_market",
            },
            "stop_loss": {
                "stop_price": stop_price,
                "limit_price": stop_price,
                "order_type": "stop_market",
            },
        }
        return self._signed_post(f"{FUTURES_PREFIX}/positions/create_tpsl", body)

    def exit_position(self, position_id: str) -> Any:
        return self._signed_post(f"{FUTURES_PREFIX}/positions/exit", {"id": position_id})
