"""One bounded, no-network paper cycle. Never submits exchange orders."""
from __future__ import annotations

from app.config import check_paper_only
from app.reporting import write_report
from app.strategy_smc import evaluate_signal


def run_cycle() -> int:
    check_paper_only()
    try:
        evaluate_signal([])
    except NotImplementedError:
        report = write_report(
            "BLOCKED_NO_STRATEGY",
            "Paper cycle safely stopped: market data and SMC signal logic are not implemented. No orders submitted.",
        )
        print(f"Paper cycle blocked safely; report={report}; orders_submitted=0")
        return 0
    raise RuntimeError("Unexpected strategy implementation detected; review before running.")


def main() -> None:
    raise SystemExit(run_cycle())


if __name__ == "__main__":
    main()
