"""One bounded, read-only paper audit cycle. Never evaluates unverified signals or submits orders."""
from __future__ import annotations

import time

from app.config import check_paper_only
from app.market_data import (
    MarketDataError,
    MarketDataNotConfigured,
    fetch_latest_candles,
)
from app.reporting import write_report

BAR_MS = 5 * 60 * 1000
MAX_CANDLE_AGE_MS = 10 * 60 * 1000


def _quality_problem(candles: list[dict], now_ms: int) -> str | None:
    """Return a fail-closed reason if returned 5m candles are stale or gappy."""
    if len(candles) < 8:
        return f"Need at least 8 completed 5m candles; received {len(candles)}."
    timestamps = [int(c["timestamp"]) for c in candles]
    if timestamps != sorted(set(timestamps)):
        return "Candle timestamps are duplicated or not strictly ascending."
    if now_ms - timestamps[-1] > MAX_CANDLE_AGE_MS:
        return "Newest completed candle is stale."
    gaps = [
        (left, right)
        for left, right in zip(timestamps, timestamps[1:])
        if right - left != BAR_MS
    ]
    if gaps:
        return f"Detected {len(gaps)} missing/irregular 5m interval(s); strategy evaluation blocked."
    return None


def run_cycle(data_dir: str = "data") -> int:
    """Fetch read-only candles when configured, then stop before unverified strategy logic.

    data_dir remains accepted for compatibility. The strategy is intentionally not
    evaluated until future-candle/look-ahead and original-strategy equivalence tests pass.
    """
    del data_dir
    check_paper_only()
    try:
        candles = fetch_latest_candles()
    except MarketDataNotConfigured as exc:
        report = write_report("BLOCKED_NO_MARKET_DATA", str(exc))
        print(f"Paper audit blocked; report={report}; orders_submitted=0")
        return 0
    except MarketDataError as exc:
        report = write_report("BLOCKED_MARKET_DATA_ERROR", str(exc))
        print(f"Paper audit blocked; report={report}; orders_submitted=0")
        return 0

    quality_problem = _quality_problem(candles, int(time.time() * 1000))
    if quality_problem:
        report = write_report(
            "BLOCKED_MARKET_DATA_QUALITY",
            f"{quality_problem} Received {len(candles)} completed candle(s); "
            "strategy not evaluated; orders_submitted=0.",
        )
        print(f"Paper audit blocked; report={report}; orders_submitted=0")
        return 0

    report = write_report(
        "BLOCKED_STRATEGY_UNVERIFIED",
        f"Read-only market-data check passed for {len(candles)} completed 5m candles. "
        "Strategy evaluation remains disabled until causal/look-ahead validation and "
        "comparison with the original two-month strategy/logs pass. "
        "No signals evaluated; no orders submitted.",
    )
    print(f"Paper audit passed data checks only; report={report}; orders_submitted=0")
    return 0


def main() -> None:
    raise SystemExit(run_cycle())


if __name__ == "__main__":
    main()
