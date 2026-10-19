"""Paper-only strategy cycle. No network access and no exchange order methods."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from app.config import check_paper_only
from app.reporting import write_report
from app.strategy_smc import evaluate_signal

def _load_candles(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Cannot read candle fixture {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list of OHLC candles")
    return payload

def run_cycle(candles_5m=None, candles_1h=None, data_dir: str = "data") -> int:
    """Run one bounded offline paper analysis cycle; never fetches live data."""
    check_paper_only()
    try:
        if candles_5m is None or candles_1h is None:
            root = Path(data_dir)
            candles_5m = _load_candles(root / "5m.json")
            candles_1h = _load_candles(root / "1h.json")
        signal = evaluate_signal(candles_5m, candles_1h)
    except FileNotFoundError:
        report = write_report("BLOCKED_NO_CANDLE_DATA",
            "No offline fixtures. Provide completed candles in data/5m.json and data/1h.json. No orders submitted.")
        print(f"Paper cycle blocked: missing candle data; report={report}; orders_submitted=0")
        return 0
    except (ValueError, KeyError, TypeError) as exc:
        report = write_report("BLOCKED_INVALID_DATA", f"Input validation failed: {exc}. No orders submitted.")
        print(f"Paper cycle blocked: invalid data; report={report}; orders_submitted=0")
        return 0
    if signal is None:
        report = write_report("NO_SIGNAL", "No valid SMC signal in supplied completed candles. No orders submitted.")
        print(f"No signal; report={report}; orders_submitted=0")
        return 0
    report = write_report("SIGNAL_ONLY_LIMIT_NOT_SUBMITTED",
        "Signal found; this cycle does not simulate a fill or submit orders. " + json.dumps(signal, sort_keys=True))
    print(f"Signal recorded for review; report={report}; orders_submitted=0")
    return 0

def main() -> None:
    raise SystemExit(run_cycle())

if __name__ == "__main__":
    main()
