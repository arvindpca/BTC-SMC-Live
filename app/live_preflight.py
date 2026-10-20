"""Live-readiness checks and execution mode validator.

Supports both PAPER and LIVE execution modes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    checks: tuple[str, ...]
    blockers: tuple[str, ...]


def run_preflight() -> PreflightResult:
    checks: list[str] = []
    blockers: list[str] = []

    mode = os.getenv("EXECUTION_MODE", "PAPER").strip().upper()
    if mode == "LIVE":
        checks.append("execution mode is LIVE (Ready for real order placement)")
    elif mode == "PAPER":
        checks.append("execution mode is PAPER")
    else:
        blockers.append(f"Invalid EXECUTION_MODE '{mode}' specified. Must be PAPER or LIVE.")

    # Only report presence; never print secret values.
    for name in ("COINDCX_API_KEY", "COINDCX_API_SECRET"):
        if os.getenv(name, "").strip():
            checks.append(f"{name} is present")
        else:
            blockers.append(f"{name} is missing")

    instrument = os.getenv("COINDCX_FUTURES_INSTRUMENT", "").strip()
    if not instrument:
        blockers.append("COINDCX_FUTURES_INSTRUMENT is missing; verify the exact active INR futures instrument first")
    elif not re.fullmatch(r"[A-Za-z0-9_:-]{3,80}", instrument):
        blockers.append("COINDCX_FUTURES_INSTRUMENT contains unexpected characters")
    else:
        checks.append(f"instrument identifier format looks plausible: {instrument}")

    margin_currency = os.getenv("COINDCX_MARGIN_CURRENCY", "").strip().upper()
    if margin_currency != "INR":
        blockers.append("COINDCX_MARGIN_CURRENCY must be set to INR for this project")
    else:
        checks.append("configured margin currency is INR")

    if os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip():
        checks.append("Telegram credentials are present (values not displayed)")
    else:
        checks.append("Telegram is not configured; alerts are optional at this stage")

    return PreflightResult(not blockers, tuple(checks), tuple(blockers))


def main() -> int:
    result = run_preflight()
    print("LIVE/PAPER PREFLIGHT EXECUTION CHECK")
    for item in result.checks:
        print(f"PASS/CHECK: {item}")
    for item in result.blockers:
        print(f"BLOCKER: {item}")
    
    if result.ok:
        print("Preflight passed successfully. Live/Paper configuration is valid.")
    else:
        print("Preflight failed due to blockers above.")
        
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
