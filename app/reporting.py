"""Write small UTF-8 paper-run reports."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def write_report(status: str, detail: str) -> Path:
    out = Path("reports")
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = out / f"paper-run-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(
        json.dumps(
            {
                "timestamp_utc": now.isoformat(),
                "mode": "PAPER",
                "status": status,
                "detail": detail,
                "orders_submitted": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
