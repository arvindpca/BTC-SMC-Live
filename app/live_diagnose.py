"""Read-only end-to-end check against the real CoinDCX API. NEVER places, edits or cancels orders.

Run via the 'BTC-SMC Live Diagnose' workflow.  Each step prints PASS/FAIL so errors can be fixed
one by one.  Secrets are never printed.
"""
from __future__ import annotations

import os
import sys
import traceback

from app.coindcx_futures import FuturesClient
from app.live_runner import ALLOWED_PAIR, compute_quantity, load_config, _quote_currency
from app.market_data import fetch_latest_candles
from app.strategy_live import scan_signals

FAILED: list[str] = []


def step(name, fn):
    try:
        out = fn()
        shown = out
        if isinstance(out, list) and out and isinstance(out[0], dict) and "close" in out[0]:
            shown = f"{len(out)} candles, last close {out[-1]['close']} at {out[-1]['timestamp']}"
        print(f"PASS  {name}" + (f" -> {shown}" if shown is not None else ""))
        return out
    except Exception as exc:  # noqa: BLE001 - diagnostic must continue
        FAILED.append(name)
        print(f"FAIL  {name} -> {type(exc).__name__}: {str(exc)[:400]}")
        if os.getenv("DIAG_TRACE"):
            traceback.print_exc()
        return None


def main() -> int:
    env = dict(os.environ)
    env["LIVE_MODE"] = "DRY_RUN"  # diagnose never needs LIVE
    cfg = step("config", lambda: load_config(env) and "ok (pair locked to " + ALLOWED_PAIR + ")")
    if cfg is None:
        return 2
    config = load_config(env)
    client = FuturesClient(config.api_key, config.api_secret)

    active = step("public: active instruments", lambda: (ALLOWED_PAIR in client.active_instruments()) or (_ for _ in ()).throw(RuntimeError(f"{ALLOWED_PAIR} not active")))
    details = step("public: instrument details", lambda: client.instrument_details(ALLOWED_PAIR))
    rate = step("public: USDT/INR rate", client.usdt_inr_rate)
    c5 = step("public: 5m candles", lambda: fetch_latest_candles(pair=ALLOWED_PAIR, limit=500, resolution="5"))
    c1 = step("public: 1h candles", lambda: fetch_latest_candles(pair=ALLOWED_PAIR, limit=120, resolution="60"))
    if c5 and c1:
        step("strategy replay on real candles", lambda: (lambda s: f"{len(s)} signals in last 500 candles; latest={s[-1] if s else None}")(scan_signals(c5, c1)))
    step("signed: API key + positions (INR filter)", lambda: f"{len(client.list_positions())} position rows returned")
    step("signed: open position on pair", lambda: client.open_position(ALLOWED_PAIR) and "OPEN POSITION EXISTS (bot will skip new entries)" or "none open")
    if details and c5:
        def sizing():
            qty, info = compute_quantity(price=float(c5[-1]["close"]), margin_inr=config.margin_inr,
                                         leverage=config.leverage, quote=_quote_currency(ALLOWED_PAIR),
                                         usdt_inr=rate, details=details)
            if qty == 0.0:
                return (f"BLOCKED: Rs{config.margin_inr:.0f} x{config.leverage} is below the exchange minimum; "
                        f"need about Rs{info['margin_needed_for_min_inr']:.0f} margin at {config.leverage}x")
            return f"qty {qty} for Rs{config.margin_inr:.0f} x{config.leverage} (notional Rs{info['notional_inr']:.0f})"
        step("sizing with current price", sizing)
        step("order payload that would be sent", lambda: client.market_order_body(
            pair=ALLOWED_PAIR, side="buy", quantity=float(details.get("min_trade_size") or 0.001),
            leverage=config.leverage)["order"])
    print("\nNo orders were placed, edited or cancelled.")
    print("RESULT:", "ALL CHECKS PASSED" if not FAILED else "FAILED: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
