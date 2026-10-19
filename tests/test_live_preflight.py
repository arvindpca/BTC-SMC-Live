from app.live_preflight import run_preflight


def test_preflight_never_claims_live_execution(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("COINDCX_API_KEY", "dummy")
    monkeypatch.setenv("COINDCX_API_SECRET", "dummy")
    monkeypatch.setenv("COINDCX_FUTURES_INSTRUMENT", "BTC_INR_TEST")
    monkeypatch.setenv("COINDCX_MARGIN_CURRENCY", "INR")
    result = run_preflight()
    assert result.ok
    assert any("PAPER" in check for check in result.checks)


def test_missing_instrument_blocks(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "PAPER")
    monkeypatch.setenv("COINDCX_API_KEY", "dummy")
    monkeypatch.setenv("COINDCX_API_SECRET", "dummy")
    monkeypatch.delenv("COINDCX_FUTURES_INSTRUMENT", raising=False)
    monkeypatch.setenv("COINDCX_MARGIN_CURRENCY", "INR")
    result = run_preflight()
    assert not result.ok
    assert any("INSTRUMENT" in blocker for blocker in result.blockers)


def test_live_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setenv("COINDCX_API_KEY", "dummy")
    monkeypatch.setenv("COINDCX_API_SECRET", "dummy")
    monkeypatch.setenv("COINDCX_FUTURES_INSTRUMENT", "BTC_INR_TEST")
    monkeypatch.setenv("COINDCX_MARGIN_CURRENCY", "INR")
    result = run_preflight()
    assert not result.ok
    assert any("PAPER mode only" in blocker for blocker in result.blockers)
