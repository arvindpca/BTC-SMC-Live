from __future__ import annotations

import pytest

from app.config import check_paper_only
from app import paper_runner
from app.market_data import MarketDataError, MarketDataNotConfigured


def candle(ts):
    return {
        "timestamp": ts,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 3.0,
    }


def test_paper_mode_passes(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    check_paper_only()


@pytest.mark.parametrize("mode,paper_only", [("LIVE", "true"), ("PAPER", "false")])
def test_non_paper_configuration_stops(monkeypatch, mode, paper_only):
    monkeypatch.setenv("EXECUTION_MODE", mode)
    monkeypatch.setenv("PAPER_ONLY", paper_only)
    with pytest.raises(SystemExit):
        check_paper_only()


def test_runner_reports_missing_market_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    monkeypatch.setattr(
        paper_runner, "fetch_latest_candles",
        lambda: (_ for _ in ()).throw(MarketDataNotConfigured("pair not configured")),
    )
    assert paper_runner.run_cycle() == 0
    report = next((tmp_path / "reports").glob("paper-run-*.json")).read_text()
    assert '"orders_submitted": 0' in report
    assert "BLOCKED_NO_MARKET_DATA" in report


def test_runner_reports_api_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    monkeypatch.setattr(
        paper_runner, "fetch_latest_candles",
        lambda: (_ for _ in ()).throw(MarketDataError("api unavailable")),
    )
    assert paper_runner.run_cycle() == 0
    report = next((tmp_path / "reports").glob("paper-run-*.json")).read_text()
    assert "BLOCKED_MARKET_DATA_ERROR" in report
    assert '"orders_submitted": 0' in report


def test_quality_check_rejects_too_few_candles():
    rows = [candle(i * 300_000) for i in range(7)]
    assert "at least 8" in paper_runner._quality_problem(rows, 10_000_000)


def test_quality_check_rejects_gaps():
    rows = [candle(i * 300_000) for i in range(8)]
    rows[-1] = candle(rows[-1]["timestamp"] + 600_000)
    assert "missing/irregular" in paper_runner._quality_problem(rows, 10_000_000)


def test_quality_check_rejects_stale_candles():
    rows = [candle(i * 300_000) for i in range(8)]
    assert "stale" in paper_runner._quality_problem(rows, rows[-1]["timestamp"] + 700_000)


def test_good_market_data_still_blocks_unverified_strategy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    now_ms = 1_800_000_000_000
    monkeypatch.setattr(paper_runner.time, "time", lambda: now_ms / 1000)
    monkeypatch.setattr(
        paper_runner, "fetch_latest_candles",
        lambda: [candle(now_ms - (8-i) * 300_000) for i in range(8)],
    )
    assert paper_runner.run_cycle() == 0
    report = next((tmp_path / "reports").glob("paper-run-*.json")).read_text()
    assert "BLOCKED_STRATEGY_UNVERIFIED" in report
    assert '"orders_submitted": 0' in report
