"""Fail-closed configuration checks. No exchange order methods exist in this scaffold."""
from __future__ import annotations

import argparse
import os


def check_paper_only() -> None:
    mode = os.getenv("EXECUTION_MODE", "PAPER").strip().upper()
    paper_only = os.getenv("PAPER_ONLY", "true").strip().lower()
    if mode != "PAPER" or paper_only not in {"1", "true", "yes"}:
        raise SystemExit("SAFETY STOP: this scaffold permits PAPER mode only.")
    print("PAPER-ONLY CHECK PASS: live execution is not implemented.")


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
