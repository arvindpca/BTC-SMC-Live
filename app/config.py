"""Fail-closed execution configuration checks.

This project is paper-only. This module does not authenticate with an exchange
or place orders; setting environment variables cannot enable live execution.
"""
from __future__ import annotations

import argparse
import os


_TRUE_VALUES = {"1", "true", "yes"}
_FALSE_VALUES = {"0", "false", "no"}


def _read_bool(name: str, default: str = "true") -> bool:
    """Read a boolean environment variable without silently accepting typos."""
    raw = os.getenv(name, default).strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise SystemExit(
        f"CONFIGURATION ERROR: {name} must be one of "
        f"{sorted(_TRUE_VALUES | _FALSE_VALUES)}; received {raw!r}."
    )


def check_paper_only() -> None:
    """Fail closed unless the explicitly supported PAPER-only mode is selected."""
    mode = os.getenv("EXECUTION_MODE", "PAPER").strip().upper()
    paper_only = _read_bool("PAPER_ONLY", "true")

    if mode != "PAPER":
        raise SystemExit(
            "SAFETY STOP: only EXECUTION_MODE=PAPER is supported; "
            "live execution is not implemented."
        )
    if not paper_only:
        raise SystemExit(
            "SAFETY STOP: PAPER_ONLY must be enabled for this project."
        )

    print(
        "PAPER-ONLY CHECK PASS: configuration is paper-only. "
        "No exchange authentication or order execution was tested."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate paper-only execution configuration."
    )
    parser.add_argument(
        "--check-paper-only",
        action="store_true",
        help="Validate that execution is restricted to PAPER mode.",
    )
    args = parser.parse_args()

    if not args.check_paper_only:
        parser.error("Use --check-paper-only")
    check_paper_only()


if __name__ == "__main__":
    main()
