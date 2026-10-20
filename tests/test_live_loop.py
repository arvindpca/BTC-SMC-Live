import pytest

from app.live_loop import RUN_OFFSETS, next_run_time, run_loop
from app.live_runner import ConfigError, load_config

BASE = 1_800_000_000 - (1_800_000_000 % 300)  # a 5-minute boundary


def cfg():
    return load_config({"LIVE_MODE": "DRY_RUN", "COINDCX_API_KEY": "k", "COINDCX_API_SECRET": "s"})


def test_next_run_time_hits_offsets_after_each_candle():
    assert next_run_time(BASE) == BASE + RUN_OFFSETS[0]
    assert next_run_time(BASE + 9) == BASE + RUN_OFFSETS[1]
    assert next_run_time(BASE + 46) == BASE + 300 + RUN_OFFSETS[0]
    assert next_run_time(BASE + 299) == BASE + 300 + RUN_OFFSETS[0]


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_loop_runs_twice_per_candle_and_stops_at_duration():
    clock = Clock(BASE + 1)
    calls = []
    stats = run_loop(cfg(), loop_seconds=900, run=lambda c: calls.append(clock.now()) or {"status": "NO_SIGNAL"},
                     now=clock.now, sleep=clock.sleep, notify=lambda m: None)
    assert stats == {"cycles": 6, "failures": 0}  # 3 candles x 2 scans
    assert calls[0] == BASE + 8 and calls[1] == BASE + 45


def test_failures_do_not_stop_the_loop_and_alert_once_after_three():
    clock = Clock(BASE + 1)
    msgs = []

    def boom(c):
        raise RuntimeError("network down")

    stats = run_loop(cfg(), loop_seconds=900, run=boom, now=clock.now, sleep=clock.sleep, notify=msgs.append)
    assert stats["cycles"] == 6 and stats["failures"] == 6
    assert len(msgs) == 1 and "3 scan cycles" in msgs[0]


def test_success_resets_failure_streak():
    clock = Clock(BASE + 1)
    msgs, n = [], {"i": 0}

    def flaky(c):
        n["i"] += 1
        if n["i"] % 3:  # fail, fail, succeed ...
            raise RuntimeError("x")
        return {"status": "NO_SIGNAL"}

    run_loop(cfg(), loop_seconds=1800, run=flaky, now=clock.now, sleep=clock.sleep, notify=msgs.append)
    assert msgs == []


def test_config_error_propagates():
    clock = Clock(BASE + 1)

    def bad(c):
        raise ConfigError("nope")

    with pytest.raises(ConfigError):
        run_loop(cfg(), loop_seconds=900, run=bad, now=clock.now, sleep=clock.sleep, notify=lambda m: None)


def test_off_mode_ends_chain(monkeypatch, tmp_path):
    import app.live_loop as ll
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("LIVE_MODE", "OFF")
    assert ll.main() == 0
    assert "continue=false" in out.read_text()
