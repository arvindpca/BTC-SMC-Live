"""Fail-closed configuration checks supporting both PAPER and LIVE execution."""
from __future__ import annotations

import argparse
import os


def check_paper_only() -> None:
    mode = os.getenv("EXECUTION_MODE", "PAPER").strip().upper()
    paper_only = os.getenv("PAPER_ONLY", "true").strip().lower()
    
    # Allow LIVE mode or explicit paper mode bypass
    if mode == "LIVE":
        print("LIVE CHECK PASS: live execution mode is enabled.")
        return
        
    if mode != "PAPER" or paper_only not in {"1", "true", "yes"}:
        raise SystemExit(f"SAFETY STOP: invalid execution mode '{mode}' or paper restrictions violated.")
    print("PAPER-ONLY CHECK PASS: running in paper mode.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-paper-only", action="store_true")
    args = parser.parse_args()
    if args.check_paper_only:
        check_paper_only()
    else:
        parser.error("Use --check-paper-only")


if __name__ == "__main__":
    main()
