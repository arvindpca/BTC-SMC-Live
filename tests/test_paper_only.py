from __future__ import annotations
import os
import pytest
from app.config import check_paper_only

def test_paper_mode_passes(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("PAPER_ONLY", "true")
    # Should not raise SystemExit
    check_paper_only()

def test_live_mode_allowed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    # With live mode enabled, check_paper_only should pass instead of exiting
    check_paper_only()

def test_invalid_mode_fails(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "INVALID_MODE")
    with pytest.raises(SystemExit):
        check_paper_only()
