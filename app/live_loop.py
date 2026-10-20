"""Continuous trading loop for ONE long GitHub Actions job.

GitHub's cron is best-effort (runs can be 20-60 minutes late), which makes a 5-minute strategy miss
signals.  Instead one job loops for ~5h40m: it scans twice per 5-minute candle (8s and 45s after the
candle closes; duplicates are blocked by the signal guard) and the workflow then dispatches its own
successor, which waits in the concurrency queue and starts the moment this job ends.

Exit code 0 always means "stopped cleanly".  The step output ``continue=true`` is written only when the
loop ran its full duration in DRY_RUN/LIVE mode, so LIVE_MODE=OFF (or a config error) ends the chain.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Any, Callable

from app.live_runner import ConfigError, LiveConfig, load_config, make_notifier, run_cycle

CANDLE_SECONDS = 300
RUN_OFFSETS = (8, 45)  # seconds after each 5-minute boundary
DEFAULT_LOOP_MINUTES = 340  # GitHub's hard job limit is 360
FAILURES_BEFORE_ALERT = 3


def next_run_time(now: float) -> float:
    """First scheduled scan time strictly after ``now``."""
    base = now - (now % CANDLE_SECONDS)
    for boundary in (base, base + CANDLE_SECONDS):
        for offset in RUN_OFFSETS:
            if boundary + offset > now:
                return boundary + offset
    raise AssertionError("unreachable")


def run_loop(
    config: LiveConfig,
    *,
    loop_seconds: float,
    run: Callable[[LiveConfig], dict[str, Any]] = run_cycle,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    notify: Callable[[str], None] | None = None,
) -> dict[str, int]:
    notify = notify or make_notifier(config)
    start = now()
    cycles = failures = consecutive = 0
    while True:
        target = next_run_time(now())
        if target - start >= loop_seconds:
            break
        sleep(max(0.0, target - now()))
        cycles += 1
        try:
            result = run(config)
            consecutive = 0
            print(f"[loop] cycle {cycles} -> {result.get('status')}")
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep trading loop alive; exchange/network can fail
            failures += 1
            consecutive += 1
            print(f"[loop] cycle {cycles} FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            traceback.print_exc(limit=3)
            if consecutive == FAILURES_BEFORE_ALERT:
                notify(f"BTC SMC LIVE: {consecutive} scan cycles in a row failed ({type(exc).__name__}). "
                       "Open positions keep their exchange-side SL/TP. Check the Actions log.")
    return {"cycles": cycles, "failures": failures}


def _set_output(name: str, value: str) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"CONFIG BLOCKED: {exc}")
        _set_output("continue", "false")
        return 2
    if config.mode == "OFF":
        print("LIVE_MODE=OFF: loop not started (no network calls, no orders).")
        _set_output("continue", "false")
        return 0

    minutes = float(os.getenv("LOOP_MINUTES", DEFAULT_LOOP_MINUTES))
    notifier = make_notifier(config)
    notifier(f"BTC SMC bot loop started: mode {config.mode}, {config.pair}, margin Rs{config.margin_inr:.0f} "
             f"x{config.leverage}, running ~{minutes / 60:.1f}h, then hands over to the next run.")
    stats = run_loop(config, loop_seconds=minutes * 60, notify=notifier)
    print(f"[loop] finished: {stats}")
    _set_output("continue", "true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
