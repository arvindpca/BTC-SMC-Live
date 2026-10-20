import app.live_diagnose as d


def test_diagnose_never_calls_order_methods(monkeypatch):
    calls = []
    import app.coindcx_futures as cf
    for name in ("create_market_order", "set_tpsl", "exit_position"):
        monkeypatch.setattr(cf.FuturesClient, name, lambda self, *a, _n=name, **k: calls.append(_n))
    monkeypatch.setenv("COINDCX_API_KEY", "k")
    monkeypatch.setenv("COINDCX_API_SECRET", "s")
    d.FAILED.clear()
    d.main()  # network fails inside the sandbox/CI proxy; steps just report FAIL
    assert calls == []
