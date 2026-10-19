import json
from app.paper_runner import run_cycle

def candle(ts, o=100, h=102, l=98, c=101):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c}

def test_runner_blocks_missing_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    assert run_cycle(data_dir="missing") == 0
    reports = list((tmp_path / "reports").glob("paper-run-*.json"))
    assert len(reports) == 1
    assert "BLOCKED_NO_CANDLE_DATA" in reports[0].read_text()

def test_runner_reports_no_signal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    bars5 = [candle(f"2026-01-{i:02d}T00:00:00Z") for i in range(1, 13)]
    bars1h = [candle(f"2026-01-{i:02d}T00:00:00Z") for i in range(1, 13)]
    assert run_cycle(bars5, bars1h) == 0
    report = next((tmp_path / "reports").glob("paper-run-*.json")).read_text()
    assert "NO_SIGNAL" in report
    assert '"orders_submitted": 0' in report

def test_json_fixture_must_be_list(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    d = tmp_path / "fixtures"
    d.mkdir()
    (d / "5m.json").write_text(json.dumps({"not": "a list"}))
    (d / "1h.json").write_text("[]")
    assert run_cycle(data_dir=str(d)) == 0
    report = next((tmp_path / "reports").glob("paper-run-*.json")).read_text()
    assert "BLOCKED_INVALID_DATA" in report
