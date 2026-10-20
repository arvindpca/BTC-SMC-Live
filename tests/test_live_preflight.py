from __future__ import annotations
import os
from app.live_preflight import run_preflight

def test_live_mode_preflight_passes(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setenv("COINDCX_API_KEY", "dummy_key")
    monkeypatch.setenv("COINDCX_API_SECRET", "dummy_secret")
    monkeypatch.setenv("COINDCX_FUTURES_INSTRUMENT", "B-BTC_USDT")
    monkeypatch.setenv("COINDCX_MARGIN_CURRENCY", "INR")
    
    result = run_preflight()
    assert result.ok is True
    assert any("LIVE" in c or "live" in c for c in result.checks)
