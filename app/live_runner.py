"""One bounded LIVE/DRY_RUN trading cycle for GitHub Actions.

Safety design (every item is covered by tests/test_live_runner.py):
  * LIVE_MODE defaults to OFF.  DRY_RUN does everything except submit orders.
    LIVE additionally needs LIVE_CONFIRM=YES_TRADE_REAL_MONEY.
  * Hard caps on margin and leverage; one position at a time (checked on the
    exchange, never assumed from local state).
  * At-most-once per signal: the signal marker is persisted BEFORE the order.
  * Only fresh signals (rejection candle among the newest candles) are traded.
  * Exchange-side SL + 4R TP are placed right after the fill.  If protection
    cannot be placed/verified, or the fill is too far from the plan, the position
    is closed immediately (fail-safe).  Python never relies on staying alive.
  * Quantity is floored to the exchange step; if that is below the exchange
    minimum, the trade is blocked and you are told the margin that would be needed.

Not implemented on purpose: 2R/3R trailing.  A GitHub-hosted job cannot monitor a
position every few seconds, so stop moves would be late.  SL and 4R TP live on the
exchange instead.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from app.coindcx_futures import CoinDCXError, FuturesClient
from app.market_data import fetch_latest_candles
from app.strategy_live import Signal, latest_signal

CONFIRM_PHRASE = "YES_TRADE_REAL_MONEY"
ALLOWED_PAIR = "B-BTC_USDT"  # hard lock: this bot trades BTC/USDT futures only
STATE_PATH = Path("state/live_state.json")
FILL_POLL_SECONDS = 20
MAX_FILL_RISK_DRIFT = 1.25  # actual risk distance may be at most 125% of planned


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveConfig:
    mode: str  # OFF | DRY_RUN | LIVE
    pair: str
    margin_inr: float
    leverage: int
    max_trades_per_day: int
    max_signal_age_bars: int
    api_key: str
    api_secret: str
    telegram_token: str
    telegram_chat: str


def load_config(env: dict[str, str] | None = None) -> LiveConfig:
    e = os.environ if env is None else env
    get = lambda k, d="": (e.get(k, d) or d).strip()  # noqa: E731

    mode = get("LIVE_MODE", "OFF").upper()
    if mode not in {"OFF", "DRY_RUN", "LIVE"}:
        raise ConfigError("LIVE_MODE must be OFF, DRY_RUN or LIVE")
    if mode == "LIVE" and get("LIVE_CONFIRM") != CONFIRM_PHRASE:
        raise ConfigError(f"LIVE mode requires LIVE_CONFIRM={CONFIRM_PHRASE}")
    if get("COINDCX_MARGIN_CURRENCY", "INR").upper() != "INR":
        raise ConfigError("COINDCX_MARGIN_CURRENCY must be INR")

    try:
        margin = float(get("MARGIN_INR", "300"))
        leverage = int(get("LEVERAGE", "30"))
        max_trades = int(get("MAX_TRADES_PER_DAY", "3"))
        age = int(get("MAX_SIGNAL_AGE_BARS", "1"))
    except ValueError as exc:
        raise ConfigError("Numeric settings must be numbers") from exc
    if not (0 < margin <= float(get("MAX_MARGIN_INR", "500"))):
        raise ConfigError("MARGIN_INR must be > 0 and <= MAX_MARGIN_INR (default cap 500)")
    if not (1 <= leverage <= 30):
        raise ConfigError("LEVERAGE must be between 1 and 30")
    if not (1 <= max_trades <= 20) or not (0 <= age <= 3):
        raise ConfigError("MAX_TRADES_PER_DAY must be 1-20 and MAX_SIGNAL_AGE_BARS 0-3")

    pair = get("COINDCX_FUTURES_PAIR") or get("COINDCX_FUTURES_INSTRUMENT") or ALLOWED_PAIR
    if mode != "OFF":
        if pair != ALLOWED_PAIR:
            raise ConfigError(f"This bot is locked to {ALLOWED_PAIR} only (got {pair!r})")
        if not get("COINDCX_API_KEY") or not get("COINDCX_API_SECRET"):
            raise ConfigError("COINDCX_API_KEY / COINDCX_API_SECRET secrets are missing")

    return LiveConfig(
        mode=mode, pair=pair, margin_inr=margin, leverage=leverage,
        max_trades_per_day=max_trades, max_signal_age_bars=age,
        api_key=get("COINDCX_API_KEY"), api_secret=get("COINDCX_API_SECRET"),
        telegram_token=get("TELEGRAM_BOT_TOKEN"), telegram_chat=get("TELEGRAM_CHAT_ID"),
    )


# ---- small helpers ------------------------------------------------------
def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        raise ValueError("step must be positive")
    d_step = Decimal(str(step))
    units = (Decimal(str(value)) / d_step).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * d_step)


def decimals_of(step: float | None) -> int:
    if not step or step <= 0:
        return 2
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def round_price(price: float, tick: float | None) -> float:
    d = decimals_of(tick)
    if tick and tick > 0:
        return round(round(price / tick) * tick, d)
    return round(price, d)


def compute_quantity(
    *, price: float, margin_inr: float, leverage: int, quote: str,
    usdt_inr: float | None, details: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """Return (quantity, info).  quantity == 0.0 means below exchange minimum."""
    price_inr = price if quote == "INR" else price * float(usdt_inr or 0)
    if price_inr <= 0:
        raise ValueError("Cannot convert price to INR")
    step = float(details.get("quantity_increment") or details.get("step") or 0)
    minimum = max(
        float(details.get("min_trade_size") or 0),
        float(details.get("min_quantity") or 0),
    )
    if step <= 0:
        raise ValueError("Instrument has no quantity_increment; refusing to guess")
    raw = margin_inr * leverage / price_inr
    qty = floor_to_step(raw, step)
    min_notional = details.get("min_notional")
    below_notional = min_notional not in (None, 0, "0") and qty * price_inr < float(min_notional)
    info = {
        "notional_inr": margin_inr * leverage,
        "price_inr": price_inr,
        "raw_qty": raw,
        "min_qty": minimum,
        "margin_needed_for_min_inr": minimum * price_inr / leverage if minimum else 0.0,
    }
    if qty <= 0 or qty < minimum or below_notional:
        return 0.0, info
    return qty, info


# ---- state ---------------------------------------------------------------
def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"last_signal_bar_time": 0, "day": "", "trades_today": 0}


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---- notifications -------------------------------------------------------
def make_notifier(config: LiveConfig) -> Callable[[str], None]:
    def notify(message: str) -> None:
        print(message)
        if not config.telegram_token or not config.telegram_chat:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
                json={"chat_id": config.telegram_chat, "text": message},
                timeout=15,
            ).raise_for_status()
        except requests.RequestException as exc:  # never print the URL (contains the token)
            print(f"Telegram send failed: {type(exc).__name__}")

    return notify


# ---- core cycle ----------------------------------------------------------
def _quote_currency(pair: str) -> str:
    quote = pair.split("_", 1)[1].upper()
    if quote not in {"INR", "USDT"}:
        raise ConfigError(f"Unsupported quote currency in {pair}")
    return quote


def _wait_for_position(client: FuturesClient, pair: str, sleep: Callable[[float], None]) -> dict[str, Any] | None:
    for _ in range(FILL_POLL_SECONDS // 2):
        position = client.open_position(pair)
        if position:
            return position
        sleep(2)
    return None


def manage_open_trade(config, client, c5, state_path: Path, notify) -> dict[str, Any]:
    """Paper-bot trailing: price reaches 2R -> SL to 2R; reaches 3R -> SL to 3R; 4R is the exchange TP.

    Uses completed 5m candles AFTER the entry candle (same as the paper bot, so a milestone never
    applies retroactively inside the candle that created it).  If moving the stop fails, the
    original exchange-side stop stays in force and you are alerted.
    """
    state = load_state(state_path)
    trade = state.get("open_trade")
    if not trade:
        return {"status": "TRADE_CLOSED", "orders_submitted": 0}
    try:
        position = client.open_position(config.pair)
    except CoinDCXError as exc:
        notify(f"BTC SMC LIVE: could not read position to manage trade ({exc}). Exchange SL/TP still active.")
        return {"status": "POSITION_CHECK_FAILED", "orders_submitted": 0}
    if position is None:
        notify(
            f"BTC SMC LIVE: {trade['side']} trade closed on exchange (stop / trailing stop / 4R target). "
            f"Entry {trade['entry']:.2f}, last max RR reached: {trade['max_rr']}. Check CoinDCX for exact P/L."
        )
        state["open_trade"] = None
        save_state(state, state_path)
        return {"status": "TRADE_CLOSED", "orders_submitted": 0}

    is_long = trade["side"] == "LONG"
    direction = 1 if is_long else -1
    entry, risk = float(trade["entry"]), float(trade["initial_risk"])
    later = [c for c in c5 if int(c["timestamp"]) > int(trade["entry_bar_time"])]
    if later:
        best = max(float(c["high"]) for c in later) if is_long else min(float(c["low"]) for c in later)
        for rr in (3, 2):
            if rr <= int(trade["max_rr"]):
                break
            milestone = entry + direction * rr * risk
            if (best >= milestone) if is_long else (best <= milestone):
                try:
                    details = client.instrument_details(config.pair)
                    tick = details.get("price_increment") or details.get("tick_size")
                    new_stop = round_price(milestone, float(tick) if tick else None)
                    client.set_tpsl(
                        position_id=str(position.get("id") or trade["position_id"]),
                        stop_price=new_stop, target_price=float(trade["target"]),
                    )
                except (CoinDCXError, ValueError) as exc:
                    notify(f"BTC SMC LIVE: reached {rr}R but moving SL failed ({exc}). Original SL still active - move it manually if needed.")
                    return {"status": "TRAIL_FAILED", "orders_submitted": 0}
                trade["max_rr"], trade["stop"] = rr, new_stop
                state["open_trade"] = trade
                save_state(state, state_path)
                notify(
                    f"BTC SMC LIVE - TRAILING STOP UPDATED\nSide: {trade['side']}\nReached: 1:{rr}\n"
                    f"New SL: {new_stop}\nFinal target 1:4: {trade['target']}\nPosition still 100% open"
                )
                return {"status": "TRAIL_UPDATED", "orders_submitted": 1, "rr": rr}
    return {"status": "POSITION_MANAGED", "orders_submitted": 0}


def run_cycle(
    config: LiveConfig,
    *,
    client: FuturesClient | None = None,
    fetch: Callable[..., list[dict[str, Any]]] = fetch_latest_candles,
    state_path: Path = STATE_PATH,
    notify: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: float | None = None,
) -> dict[str, Any]:
    notify = notify or make_notifier(config)
    if config.mode != "OFF" and config.pair != ALLOWED_PAIR:
        raise ConfigError(f"This bot is locked to {ALLOWED_PAIR} only")
    if config.mode == "OFF":
        print("LIVE_MODE=OFF: nothing to do (no network calls, no orders).")
        return {"status": "OFF", "orders_submitted": 0}

    now_ts = time.time() if now is None else now
    c5 = fetch(pair=config.pair, limit=500, now_seconds=int(now_ts), resolution="5")
    c1 = fetch(pair=config.pair, limit=120, now_seconds=int(now_ts), resolution="60")
    if config.mode == "LIVE" and load_state(state_path).get("open_trade"):
        client = client or FuturesClient(config.api_key, config.api_secret)
        managed = manage_open_trade(config, client, c5, state_path, notify)
        if managed["status"] != "TRADE_CLOSED":
            return managed
    signal = latest_signal(c5, c1, max_age_bars=config.max_signal_age_bars)
    if signal is None:
        print("No fresh SMC signal on the latest completed candles.")
        return {"status": "NO_SIGNAL", "orders_submitted": 0}

    state = load_state(state_path)
    today = datetime.fromtimestamp(now_ts, timezone.utc).strftime("%Y-%m-%d")
    if state.get("day") != today:
        state.update(day=today, trades_today=0)
    if signal.bar_time <= int(state.get("last_signal_bar_time", 0)):
        print("Signal already handled earlier (duplicate guard).")
        return {"status": "DUPLICATE_SIGNAL", "orders_submitted": 0}
    if state["trades_today"] >= config.max_trades_per_day:
        notify(f"BTC SMC LIVE: daily trade cap ({config.max_trades_per_day}) reached; signal skipped.")
        return {"status": "DAILY_CAP", "orders_submitted": 0}

    client = client or FuturesClient(config.api_key, config.api_secret)
    try:
        if config.pair not in client.active_instruments():
            raise CoinDCXError(f"{config.pair} is not an active futures instrument")
        if client.open_position(config.pair):
            print("A position is already open on the exchange; signal skipped.")
            return {"status": "POSITION_OPEN", "orders_submitted": 0}
        details = client.instrument_details(config.pair)
        quote = _quote_currency(config.pair)
        usdt_inr = client.usdt_inr_rate() if quote == "USDT" else None
        qty, info = compute_quantity(
            price=signal.entry, margin_inr=config.margin_inr, leverage=config.leverage,
            quote=quote, usdt_inr=usdt_inr, details=details,
        )
    except (CoinDCXError, ValueError, ConfigError) as exc:
        notify(f"BTC SMC LIVE: pre-trade check failed, no order sent. Reason: {exc}")
        return {"status": "PRETRADE_FAILED", "orders_submitted": 0, "error": str(exc)}

    side = "buy" if signal.side == "LONG" else "sell"
    # Technical guard (not a strategy filter): with isolated margin the position is liquidated
    # at roughly 100/leverage % against you, so a stop wider than that can never trigger.
    liq_limit = 90.0 / config.leverage
    if signal.risk_pct >= liq_limit:
        notify(
            f"BTC SMC LIVE: signal skipped - stop is {signal.risk_pct:.2f}% away but {config.leverage}x "
            f"liquidates at about {100.0 / config.leverage:.2f}%, so the stop could never trigger.\n"
            f"{signal.side} ref entry {signal.entry:.2f} | SL {signal.stop:.2f}"
        )
        state["last_signal_bar_time"] = signal.bar_time
        save_state(state, state_path)
        return {"status": "SKIPPED_STOP_BEYOND_LIQUIDATION", "orders_submitted": 0}
    summary = (
        f"{signal.side} {config.pair}\nSignal candle: {datetime.fromtimestamp(signal.bar_time/1000, timezone.utc).isoformat()}\n"
        f"Ref entry {signal.entry:.2f} | SL {signal.stop:.2f} | TP(4R) {signal.target:.2f} | risk {signal.risk_pct:.2f}%"
    )
    if qty == 0.0:
        notify(
            "BTC SMC LIVE: signal BLOCKED - quantity below exchange minimum.\n" + summary +
            f"\nMargin Rs{config.margin_inr:.0f} x{config.leverage} = Rs{info['notional_inr']:.0f} notional; "
            f"minimum order needs about Rs{info['margin_needed_for_min_inr']:.0f} margin at {config.leverage}x."
        )
        state["last_signal_bar_time"] = signal.bar_time
        save_state(state, state_path)
        return {"status": "BLOCKED_MIN_ORDER", "orders_submitted": 0, "info": info}

    body = client.market_order_body(pair=config.pair, side=side, quantity=qty, leverage=config.leverage)
    if config.mode == "DRY_RUN":
        notify("BTC SMC DRY RUN - would place MARKET order (nothing sent)\n" + summary +
               f"\nQty {qty} | margin Rs{config.margin_inr:.0f} x{config.leverage}\nPayload: {json.dumps(body['order'])}")
        return {"status": "DRY_RUN_WOULD_ORDER", "orders_submitted": 0, "body": body}

    # ---------------- LIVE ----------------
    state["last_signal_bar_time"] = signal.bar_time  # at-most-once: mark BEFORE ordering
    state["trades_today"] += 1
    save_state(state, state_path)
    try:
        client.create_market_order(pair=config.pair, side=side, quantity=qty, leverage=config.leverage)
    except CoinDCXError as exc:
        notify(f"BTC SMC LIVE: ORDER REJECTED, nothing opened. {exc}\n{summary}")
        return {"status": "ORDER_REJECTED", "orders_submitted": 1, "error": str(exc)}

    position = _wait_for_position(client, config.pair, sleep)
    if position is None:
        notify("BTC SMC LIVE: CRITICAL - order sent but no position found. CHECK COINDCX MANUALLY NOW.\n" + summary)
        return {"status": "CRITICAL_NO_POSITION_FOUND", "orders_submitted": 1}
    return _protect_or_exit(client, config, signal, position, state, notify, summary, details, state_path)


def _protect_or_exit(client, config, signal: Signal, position, state, notify, summary, details, state_path=STATE_PATH):
    position_id = str(position.get("id"))
    fill = float(position.get("avg_price") or 0)
    is_long = signal.side == "LONG"
    planned_risk = abs(signal.entry - signal.stop)
    actual_risk = (fill - signal.stop) if is_long else (signal.stop - fill)
    tick = details.get("price_increment") or details.get("tick_size")
    tick = float(tick) if tick else None

    def fail_safe(reason: str) -> dict[str, Any]:
        try:
            client.exit_position(position_id)
            notify(f"BTC SMC LIVE: FAIL-SAFE EXIT executed ({reason}).\n{summary}")
            return {"status": "FAILSAFE_EXITED", "orders_submitted": 2, "reason": reason}
        except CoinDCXError as exc:
            notify(f"BTC SMC LIVE: CRITICAL - UNPROTECTED POSITION ({reason}); exit failed: {exc}. CLOSE MANUALLY NOW.\n{summary}")
            return {"status": "CRITICAL_UNPROTECTED", "orders_submitted": 2, "reason": reason}

    if fill <= 0 or actual_risk <= 0 or actual_risk > planned_risk * MAX_FILL_RISK_DRIFT:
        return fail_safe(f"fill {fill} too far from plan (risk {actual_risk:.2f} vs planned {planned_risk:.2f})")

    stop = round_price(signal.stop, tick)
    target = round_price(fill + 4 * actual_risk if is_long else fill - 4 * actual_risk, tick)
    try:
        client.set_tpsl(position_id=position_id, stop_price=stop, target_price=target)
    except CoinDCXError as exc:
        return fail_safe(f"SL/TP placement failed: {exc}")

    note = ""
    try:
        check = client.open_position(config.pair)
        if check is None:
            note = "\nPosition already closed right after protection (SL/TP hit or exited)."
        elif "stop_loss_trigger" in check and not check.get("stop_loss_trigger"):
            return fail_safe("stop loss not visible on exchange after placement")
        elif "stop_loss_trigger" not in check:
            note = "\nWARNING: could not verify SL via positions API (field absent); relying on API success response."
    except CoinDCXError as exc:
        note = f"\nWARNING: SL verification call failed ({exc}); relying on API success response."

    state["open_trade"] = {
        "side": signal.side, "entry": fill, "initial_risk": actual_risk, "stop": stop,
        "target": target, "entry_bar_time": signal.bar_time, "max_rr": 0,
        "position_id": position_id,
    }
    save_state(state, state_path)
    notify(
        "BTC SMC LIVE ENTRY - protected\n" + summary +
        f"\nFill {fill:.2f} | exchange SL {stop} | exchange TP(4R) {target}"
        f"\nMargin Rs{config.margin_inr:.0f} x{config.leverage}{note}"
        "\nAt 2R: SL -> 2R | at 3R: SL -> 3R | exit at 4R (no partial exits)"
    )
    return {"status": "ORDER_PLACED_PROTECTED", "orders_submitted": 2, "fill": fill, "sl": stop, "tp": target}


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"CONFIG BLOCKED: {exc}")
        return 2
    result = run_cycle(config)
    print(f"RESULT: {json.dumps({k: v for k, v in result.items() if k != 'body'})}")
    return 3 if str(result.get("status", "")).startswith("CRITICAL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
