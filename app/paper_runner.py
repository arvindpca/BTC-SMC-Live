"""Read-only, paper-only SMC scan cycle.

Fetches completed public candles and reports the latest signal candidate. It does
not create virtual fills, call authenticated endpoints, or submit exchange orders.
"""
from __future__ import annotations

import json
import os

from app.config import check_paper_only
from app.market_data import MarketDataError, MarketDataNotConfigured, fetch_latest_candles
from app.reporting import write_report
from app.strategy_live import analyze, format_scan_report, latest_signal


def run_cycle(data_dir: str = "data") -> int:
    # data_dir retained for backwards compatibility with existing callers.
    del data_dir
    check_paper_only()
    pair = os.getenv("COINDCX_FUTURES_PAIR", "").strip()
    if not pair:
        report = write_report(
            "BLOCKED_NO_INSTRUMENT",
            "Set COINDCX_FUTURES_PAIR to an exact verified CoinDCX futures pair. "
            "No candles evaluated; no orders submitted.",
        )
        print(f"Paper scan blocked safely; report={report}; orders_submitted=0")
        return 0

    try:
        candles_5m = fetch_latest_candles(pair=pair, limit=150, resolution="5")
        candles_1h = fetch_latest_candles(pair=pair, limit=100, resolution="60")
        signals, info = analyze(candles_5m, candles_1h)
        fresh = latest_signal(candles_5m, candles_1h, max_age_bars=0)
    except (MarketDataError, MarketDataNotConfigured, ValueError, KeyError) as exc:
        report = write_report(
            "BLOCKED_MARKET_DATA",
            f"Read-only candle/strategy validation failed: {type(exc).__name__}: {exc}. "
            "No signal accepted; no orders submitted.",
        )
        print(f"Paper scan blocked safely; report={report}; orders_submitted=0")
        return 0

    detail = [
        f"instrument={pair}",
        f"completed_5m_candles={len(candles_5m)}",
        f"completed_1h_candles={len(candles_1h)}",
        f"signals_in_lookback={len(signals)}",
        format_scan_report(info),
    ]
    if fresh is not None:
        detail.extend([
            "fresh_signal_candidate=YES",
            f"side={fresh.side}",
            f"signal_bar_time_ms={fresh.bar_time}",
            f"reference_entry={fresh.entry}",
            f"stop_loss_reference={fresh.stop}",
            f"take_profit_reference_4R={fresh.target}",
            f"risk_pct={fresh.risk_pct:.6f}",
            "note=signal candidate only; no virtual fill or exchange order",
        ])
        status = "SIGNAL_CANDIDATE"
    else:
        detail.append("fresh_signal_candidate=NO")
        detail.append("note=read-only scan only; no virtual fill or exchange order")
        status = "NO_FRESH_SIGNAL"

    report = write_report(status, "\n".join(detail))
    print("\n".join(detail))
    print(f"Paper scan complete; report={report}; orders_submitted=0")
    return 0


def main() -> None:
    raise SystemExit(run_cycle())


if __name__ == "__main__":
    main()
