from types import SimpleNamespace

import app.paper_runner as runner


def test_cycle_reports_fresh_signal_without_submitting_orders(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPER_ONLY", "true")
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("COINDCX_FUTURES_PAIR", "B-BTC_USDT")
    monkeypatch.setattr(runner, "fetch_latest_candles", lambda **kwargs: [
        {"timestamp": 1_800_000_000_000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}
    ])
    monkeypatch.setattr(runner, "analyze", lambda c5, h1: ([], {
        "candles_5m": 1, "first_bar_time": 1, "last_bar_time": 1, "last_close": 100,
        "h1_closed": 1, "last_h1_time": 1, "bias": "NEUTRAL", "swing_high": None,
        "swing_low": None, "setup": None, "signals_in_window": 0, "events": [],
        "event_counts": {},
    }))
    monkeypatch.setattr(runner, "format_scan_report", lambda info: "scan diagnostic")
    monkeypatch.setattr(runner, "latest_signal", lambda *a, **k: SimpleNamespace(
        side="SHORT", bar_time=123, entry=100.0, stop=101.0, target=96.0, risk_pct=1.0
    ))

    assert runner.run_cycle() == 0
    reports = list((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")
    assert "SIGNAL_CANDIDATE" in body
    assert "stop_loss_reference" in body
    assert '"orders_submitted": 0' in body


def test_cycle_blocks_without_pair(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPER_ONLY", "true")
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.delenv("COINDCX_FUTURES_PAIR", raising=False)

    assert runner.run_cycle() == 0
    body = next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8")
    assert "BLOCKED_NO_MARKET_DATA" in body
    assert '"orders_submitted": 0' in body


def test_cycle_fails_closed_on_market_data_error(monkeypatch, tmp_path):
    from app.market_data import MarketDataError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPER_ONLY", "true")
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("COINDCX_FUTURES_PAIR", "B-BTC_USDT")

    def fail(**kwargs):
        raise MarketDataError("test network failure")

    monkeypatch.setattr(runner, "fetch_latest_candles", fail)
    assert runner.run_cycle() == 0
    body = next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8")
    assert "BLOCKED_MARKET_DATA" in body
    assert '"orders_submitted": 0' in body
