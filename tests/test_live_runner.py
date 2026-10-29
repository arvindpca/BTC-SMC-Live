from pathlib import Path

import pytest

import app.live_runner as lr
from app.coindcx_futures import CoinDCXError, FuturesClient
from app.live_runner import (
    CONFIRM_PHRASE,
    ConfigError,
    compute_quantity,
    floor_to_step,
    load_config,
    round_price,
    run_cycle,
)
from app.strategy_live import Signal

BASE_ENV = {
    "COINDCX_API_KEY": "k",
    "COINDCX_API_SECRET": "s",
    "COINDCX_FUTURES_PAIR": "B-BTC_USDT",
}
BTC_DETAILS = {"min_trade_size": 0.001, "quantity_increment": 0.001, "price_increment": 0.1}


def cfg(**extra):
    return load_config({**BASE_ENV, **extra})


# ---------------- configuration gates ----------------
def test_default_mode_is_off_and_needs_no_secrets():
    config = load_config({})
    assert config.mode == "OFF"


def test_live_requires_confirmation_phrase():
    with pytest.raises(ConfigError):
        cfg(LIVE_MODE="LIVE")
    with pytest.raises(ConfigError):
        cfg(LIVE_MODE="LIVE", LIVE_CONFIRM="yes")
    assert cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE).mode == "LIVE"


@pytest.mark.parametrize(
    "extra",
    [
        {"LIVE_MODE": "DRY_RUN", "MARGIN_INR": "501"},
        {"LIVE_MODE": "DRY_RUN", "MARGIN_INR": "0"},
        {"LIVE_MODE": "DRY_RUN", "LEVERAGE": "31"},
        {"LIVE_MODE": "DRY_RUN", "COINDCX_MARGIN_CURRENCY": "USDT"},
        {"LIVE_MODE": "DRY_RUN", "COINDCX_FUTURES_PAIR": "BTCINR"},
        {"LIVE_MODE": "DRY_RUN", "COINDCX_FUTURES_PAIR": "B-ETH_USDT"},
        {"LIVE_MODE": "DRY_RUN", "COINDCX_FUTURES_PAIR": "B-BTC_INR"},
        {"LIVE_MODE": "NONSENSE"},
    ],
)
def test_bad_config_blocked(extra):
    with pytest.raises(ConfigError):
        cfg(**extra)


def test_missing_secrets_blocked_when_not_off():
    with pytest.raises(ConfigError):
        load_config({"LIVE_MODE": "DRY_RUN", "COINDCX_FUTURES_PAIR": "B-BTC_USDT"})


# ---------------- sizing ----------------
def test_floor_and_round_helpers():
    assert floor_to_step(0.00279, 0.001) == 0.002
    assert floor_to_step(1.0, 0.001) == 1.0
    assert round_price(100.04, 0.1) == 100.0
    assert round_price(100.06, 0.1) == 100.1


def test_rs200_x30_is_below_btc_minimum_and_reports_needed_margin():
    qty, info = compute_quantity(
        price=100_000, margin_inr=200, leverage=30, quote="USDT", usdt_inr=88.0, details=BTC_DETAILS
    )
    assert qty == 0.0  # Rs6,000 notional buys only ~0.0007 BTC < 0.001 minimum
    assert info["margin_needed_for_min_inr"] == pytest.approx(0.001 * 100_000 * 88 / 30)


def test_sizing_ok_when_margin_large_enough():
    qty, _ = compute_quantity(
        price=100_000, margin_inr=2000, leverage=30, quote="USDT", usdt_inr=88.0, details=BTC_DETAILS
    )
    assert qty == 0.006  # floor(60000 / 8.8e6 ... = 0.00681) -> 0.006
    assert qty * 100_000 * 88 / 30 <= 2000


def test_inr_quoted_pair_needs_no_conversion():
    qty, info = compute_quantity(
        price=8_800_000, margin_inr=2000, leverage=30, quote="INR", usdt_inr=None, details=BTC_DETAILS
    )
    assert qty == 0.006 and info["price_inr"] == 8_800_000


def test_refuses_to_guess_missing_step():
    with pytest.raises(ValueError):
        compute_quantity(price=1, margin_inr=100, leverage=1, quote="INR", usdt_inr=None, details={})


# ---------------- cycle with a fake exchange ----------------
class FakeExchange(FuturesClient):
    def __init__(self, *, positions=None, fill=100_000.0, fail_tpsl=False, fail_order=False,
                 details=None, sl_visible=True):
        super().__init__("k", "s")
        self.calls = []
        self.positions = list(positions or [])
        self.fill, self.fail_tpsl, self.fail_order = fill, fail_tpsl, fail_order
        self.details = details or BTC_DETAILS
        self.sl_visible = sl_visible
        self.opened = None

    def active_instruments(self):
        return ["B-BTC_USDT"]

    def instrument_details(self, pair):
        return self.details

    def usdt_inr_rate(self):
        return 88.0

    def list_positions(self):
        if self.opened:
            return [self.opened]
        return self.positions

    def create_market_order(self, **kw):
        self.calls.append(("order", kw))
        if self.fail_order:
            raise CoinDCXError("HTTP 400 rejected")
        self.opened = {"pair": "B-BTC_USDT", "active_pos": kw["quantity"], "id": "pos1",
                       "avg_price": self.fill,
                       "stop_loss_trigger": 99_000.0 if self.sl_visible else None}

    def set_tpsl(self, **kw):
        self.calls.append(("tpsl", kw))
        if self.fail_tpsl:
            raise CoinDCXError("HTTP 400 tpsl")

    def exit_position(self, position_id):
        self.calls.append(("exit", position_id))
        self.opened = None

    def kinds(self):
        return [c[0] for c in self.calls]


def make_signal(side="LONG", entry=100_000.0, stop=99_000.0):
    risk = abs(entry - stop)
    target = entry + 4 * risk if side == "LONG" else entry - 4 * risk
    return Signal(side=side, entry=entry, stop=stop, target=target, risk_pct=risk / entry * 100,
                  bar_time=1_800_000_000_000, bar_index=499, bias=side)


def run(monkeypatch, tmp_path, config, exchange, signal, **kw):
    monkeypatch.setattr(lr, "latest_signal", lambda *a, **k: signal)
    messages = []
    result = run_cycle(
        config, client=exchange, fetch=lambda **k: [], state_path=tmp_path / "state.json",
        notify=messages.append, sleep=lambda s: None, now=1_800_000_300, **kw,
    )
    return result, messages


BIG = {"MARGIN_INR": "500"}  # cap-limited; use a details set with a smaller minimum below
SMALL_MIN = {"min_trade_size": 0.0001, "quantity_increment": 0.0001, "price_increment": 0.1}


def test_off_mode_does_nothing(monkeypatch, tmp_path):
    ex = FakeExchange()
    result, _ = run(monkeypatch, tmp_path, load_config({}), ex, make_signal())
    assert result["status"] == "OFF" and ex.calls == []


def test_no_signal_places_nothing(monkeypatch, tmp_path):
    ex = FakeExchange()
    result, _ = run(monkeypatch, tmp_path, cfg(LIVE_MODE="DRY_RUN"), ex, None)
    assert result["status"] == "NO_SIGNAL" and ex.calls == []


def test_dry_run_never_submits_orders(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    result, msgs = run(monkeypatch, tmp_path, cfg(LIVE_MODE="DRY_RUN", **BIG), ex, make_signal())
    assert result["status"] == "DRY_RUN_WOULD_ORDER"
    assert ex.calls == [] and result["orders_submitted"] == 0
    assert "nothing sent" in msgs[0]


def test_live_happy_path_places_exchange_side_sl_and_4r_tp(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, msgs = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "ORDER_PLACED_PROTECTED"
    assert ex.kinds() == ["order", "tpsl"]
    tpsl = ex.calls[1][1]
    assert tpsl["stop_price"] == 99_000.0 and tpsl["target_price"] == 104_000.0
    assert ex.calls[0][1]["quantity"] > 0 and ex.calls[0][1]["leverage"] == 30


def test_short_side_prices(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, _ = run(monkeypatch, tmp_path, config, ex, make_signal("SHORT", 100_000, 101_000))
    assert result["status"] == "ORDER_PLACED_PROTECTED"
    assert ex.calls[0][1]["side"] == "sell"
    assert ex.calls[1][1]["target_price"] == 96_000.0


def test_tpsl_failure_triggers_failsafe_exit(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, fail_tpsl=True)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "FAILSAFE_EXITED"
    assert ex.kinds() == ["order", "tpsl", "exit"]


def test_fill_far_from_plan_triggers_failsafe_exit(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, fill=98_900.0)  # stop almost reached already
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "FAILSAFE_EXITED" and "tpsl" not in ex.kinds()


def test_invisible_stop_after_placement_triggers_exit(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, sl_visible=False)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "FAILSAFE_EXITED"


def test_rejected_order_is_not_retried_next_run(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, fail_order=True)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    first, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    second, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert first["status"] == "ORDER_REJECTED"
    assert second["status"] == "DUPLICATE_SIGNAL"
    assert ex.kinds().count("order") == 1


def test_existing_position_blocks_new_order(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, positions=[{"pair": "B-BTC_USDT", "active_pos": 0.1, "id": "x"}])
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "POSITION_OPEN" and ex.calls == []


def test_below_minimum_order_is_blocked_with_explanation(monkeypatch, tmp_path):
    ex = FakeExchange(details=BTC_DETAILS)  # real BTC minimum 0.001
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, MARGIN_INR="200")
    result, msgs = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "BLOCKED_MIN_ORDER" and ex.calls == []
    assert "minimum" in msgs[0]


def test_daily_cap(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, MAX_TRADES_PER_DAY="1", **BIG)
    lr.save_state({"last_signal_bar_time": 1, "day": "2027-01-15", "trades_today": 1}, tmp_path / "state.json")
    result, _ = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "DAILY_CAP" and ex.calls == []


def test_secrets_not_in_notifications(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    _, msgs = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert all("COINDCX" not in m for m in msgs)


def test_pair_defaults_to_btc_usdt_and_is_locked():
    assert cfg(LIVE_MODE="DRY_RUN", COINDCX_FUTURES_PAIR="").pair == "B-BTC_USDT"
    from dataclasses import replace
    import app.live_runner as lr_mod
    with pytest.raises(ConfigError):
        run_cycle(replace(cfg(LIVE_MODE="DRY_RUN"), pair="B-ETH_USDT"), client=object(),
                  fetch=lambda **k: [], notify=lambda m: None)


def test_stop_beyond_liquidation_is_skipped_not_traded(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    wide = make_signal("LONG", 100_000, 95_000)  # 5% stop; 30x liquidates near 3.3%
    result, msgs = run(monkeypatch, tmp_path, config, ex, wide)
    assert result["status"] == "SKIPPED_STOP_BEYOND_LIQUIDATION" and ex.calls == []
    assert "liquidates" in msgs[0]


def test_wide_but_inside_liquidation_stop_is_traded(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, fill=100_000.0)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    sig = make_signal("LONG", 100_000, 97_500)  # 2.5%: above the old filter's edge region, below liquidation
    result, _ = run(monkeypatch, tmp_path, config, ex, sig)
    assert result["status"] == "ORDER_PLACED_PROTECTED"


# ---------------- 2R / 3R trailing (paper-bot logic) ----------------
def trade_state(side="LONG", entry=100_000.0, risk=1_000.0, max_rr=0, entry_bar=1_000):
    target = entry + 4 * risk if side == "LONG" else entry - 4 * risk
    stop = entry - risk if side == "LONG" else entry + risk
    return {"last_signal_bar_time": entry_bar, "day": "2027-01-15", "trades_today": 1,
            "open_trade": {"side": side, "entry": entry, "initial_risk": risk, "stop": stop,
                           "target": target, "entry_bar_time": entry_bar, "max_rr": max_rr,
                           "position_id": "pos1"}}


def candle(ts, high, low):
    return {"timestamp": ts, "open": 100_000.0, "high": high, "low": low, "close": 100_000.0}


def run_manage(monkeypatch, tmp_path, state, candles, exchange):
    monkeypatch.setattr(lr, "latest_signal", lambda *a, **k: None)
    lr.save_state(state, tmp_path / "state.json")
    msgs = []
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result = run_cycle(config, client=exchange, fetch=lambda **k: candles, state_path=tmp_path / "state.json",
                       notify=msgs.append, sleep=lambda s: None, now=1_800_000_300)
    return result, msgs, lr.load_state(tmp_path / "state.json")


def open_pos():
    return {"pair": "B-BTC_USDT", "active_pos": 0.006, "id": "pos1", "avg_price": 100_000.0}


def test_trail_moves_sl_to_2r(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN); ex.opened = open_pos()
    result, msgs, st = run_manage(monkeypatch, tmp_path, trade_state(),
                                  [candle(2_000, 102_100.0, 100_500.0)], ex)
    assert result["status"] == "TRAIL_UPDATED" and result["rr"] == 2
    assert ex.calls[0][0] == "tpsl" and ex.calls[0][1]["stop_price"] == 102_000.0
    assert ex.calls[0][1]["target_price"] == 104_000.0
    assert st["open_trade"]["max_rr"] == 2


def test_trail_jumps_straight_to_3r(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN); ex.opened = open_pos()
    result, _, st = run_manage(monkeypatch, tmp_path, trade_state(),
                               [candle(2_000, 103_050.0, 100_500.0)], ex)
    assert result["rr"] == 3 and ex.calls[0][1]["stop_price"] == 103_000.0 and st["open_trade"]["max_rr"] == 3


def test_no_trail_below_2r_and_never_on_entry_candle(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN); ex.opened = open_pos()
    candles = [candle(1_000, 109_000.0, 100_000.0),  # the entry candle itself: ignored
               candle(2_000, 101_900.0, 100_000.0)]
    result, _, _ = run_manage(monkeypatch, tmp_path, trade_state(), candles, ex)
    assert result["status"] == "POSITION_MANAGED" and ex.calls == []


def test_trail_is_not_repeated_for_same_milestone(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN); ex.opened = open_pos()
    result, _, _ = run_manage(monkeypatch, tmp_path, trade_state(max_rr=2),
                              [candle(2_000, 102_500.0, 100_500.0)], ex)
    assert result["status"] == "POSITION_MANAGED" and ex.calls == []


def test_short_trail(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN); ex.opened = open_pos()
    result, _, _ = run_manage(monkeypatch, tmp_path, trade_state("SHORT"),
                              [candle(2_000, 99_500.0, 97_900.0)], ex)
    assert result["rr"] == 2 and ex.calls[0][1]["stop_price"] == 98_000.0


def test_trail_failure_keeps_original_stop_and_alerts(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN, fail_tpsl=True); ex.opened = open_pos()
    result, msgs, st = run_manage(monkeypatch, tmp_path, trade_state(),
                                  [candle(2_000, 102_100.0, 100_500.0)], ex)
    assert result["status"] == "TRAIL_FAILED" and st["open_trade"]["max_rr"] == 0
    assert "Original SL still active" in msgs[0]


def test_closed_position_clears_trade_and_notifies(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)  # no open position on exchange
    result, msgs, st = run_manage(monkeypatch, tmp_path, trade_state(max_rr=2),
                                  [candle(2_000, 101_000.0, 100_000.0)], ex)
    assert result["status"] == "NO_SIGNAL"  # trade cleared, then normal scan continues
    assert st["open_trade"] is None and "closed on exchange" in msgs[0]


def test_entry_records_trade_for_trailing(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    run(monkeypatch, tmp_path, config, ex, make_signal())
    trade = lr.load_state(tmp_path / "state.json")["open_trade"]
    assert trade["max_rr"] == 0 and trade["initial_risk"] == 1_000.0 and trade["target"] == 104_000.0


# ---------------- SIGNAL_ONLY (public API only) ----------------
def test_signal_only_needs_no_api_keys():
    config = load_config({"LIVE_MODE": "SIGNAL_ONLY"})
    assert config.mode == "SIGNAL_ONLY"


def test_signal_only_sends_signal_and_never_touches_exchange(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    config = load_config({"LIVE_MODE": "SIGNAL_ONLY"})
    result, msgs = run(monkeypatch, tmp_path, config, ex, make_signal(side="SHORT", entry=100_000.0, stop=101_000.0))
    assert result["status"] == "SIGNAL_SENT" and result["orders_submitted"] == 0
    assert ex.calls == []
    assert "SHORT" in msgs[0] and "101000.00" in msgs[0] and "96000.00" in msgs[0]
    # same signal again -> duplicate guard, no second message
    result2, msgs2 = run(monkeypatch, tmp_path, config, ex, make_signal(side="SHORT", entry=100_000.0, stop=101_000.0))
    assert result2["status"] == "DUPLICATE_SIGNAL" and msgs2 == []


def test_live_key_failure_sends_signal_instead_of_order(monkeypatch, tmp_path):
    ex = FakeExchange(details=SMALL_MIN)
    monkeypatch.setattr(ex, "active_instruments", lambda: (_ for _ in ()).throw(CoinDCXError('HTTP 401: {"message":"Invalid API key"}')), raising=False)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    result, msgs = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert result["status"] == "KEY_FAILED_SIGNAL_SENT" and result["orders_submitted"] == 0
    assert "KHUD TRADE LAGAO" in msgs[0] and "settings/secrets" in msgs[0]


def test_expired_key_with_open_trade_does_not_spam_and_keeps_signalling(monkeypatch, tmp_path):
    import json as _json
    monkeypatch.setitem(lr._KEY_FAILED, "value", False)
    (tmp_path / "state.json").write_text(_json.dumps({"open_trade": {"side": "LONG"}, "last_signal_bar_time": 0}))
    err = CoinDCXError('HTTP 422: {"code":422,"message":"API key expired. Please generate new keys."}')
    ex = FakeExchange(details=SMALL_MIN)
    monkeypatch.setattr(ex, "open_position", lambda pair: (_ for _ in ()).throw(err), raising=False)
    monkeypatch.setattr(ex, "active_instruments", lambda: (_ for _ in ()).throw(err), raising=False)
    config = cfg(LIVE_MODE="LIVE", LIVE_CONFIRM=CONFIRM_PHRASE, **BIG)
    r1, m1 = run(monkeypatch, tmp_path, config, ex, make_signal())
    assert r1["status"] == "KEY_FAILED_SIGNAL_SENT"
    assert any("key kaam nahi" in m for m in m1) and any("KHUD TRADE" in m for m in m1)
    r2, m2 = run(monkeypatch, tmp_path, config, ex, make_signal())  # same candle -> silent
    assert r2["status"] == "DUPLICATE_SIGNAL" and m2 == []
    monkeypatch.setitem(lr._KEY_FAILED, "value", False)
