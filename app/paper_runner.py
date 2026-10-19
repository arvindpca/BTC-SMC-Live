"""One bounded, no-network paper cycle. Never submits exchange orders."""
from __future__ import annotations

from app.config import check_paper_only
from app.reporting import write_report


def run_cycle(data_dir: str = "data") -> int:
    """Fail closed until a verified market-data adapter and causal strategy exist.

    data_dir is accepted for compatibility with the offline fixture test. This
    runner deliberately does not read files or call an exchange yet.
    """
    check_paper_only()
    report = write_report(
        "BLOCKED_NO_MARKET_DATA",
        "No market-data adapter is configured and the SMC strategy has not passed "
        "causal/look-ahead validation. No candles evaluated; no orders submitted.",
    )
    print(f"Paper cycle blocked safely; report={report}; orders_submitted=0")
    return 0


def main() -> None:
    raise SystemExit(run_cycle())


if __name__ == "__main__":
    main()
