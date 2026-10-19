import pytest

from app.config import check_paper_only
from app.paper_runner import run_cycle


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


def test_runner_safely_reports_missing_strategy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    assert run_cycle() == 0
    reports = list((tmp_path / "reports").glob("paper-run-*.json"))
    assert len(reports) == 1
    assert '"orders_submitted": 0' in reports[0].read_text()
