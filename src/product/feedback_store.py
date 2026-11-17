"""Append-only, thread-safe recommendation feedback store (CSV)."""
from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

from src import config

FIELDS = ["timestamp", "profile_id", "career_id", "feedback_type", "comment"]
VALID_TYPES = {"useful", "not_useful", "accepted", "rejected"}
_lock = threading.Lock()


def record_feedback(profile_id: str, career_id: str, feedback_type: str, comment: str = "",
                    path: Path | None = None) -> dict:
    if feedback_type not in VALID_TYPES:
        raise ValueError(f"feedback_type must be one of {sorted(VALID_TYPES)}")
    path = Path(path or config.FEEDBACK_CSV)
    row = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "profile_id": profile_id[:120],
           "career_id": career_id[:40], "feedback_type": feedback_type, "comment": comment[:1000].replace("\n", " ")}
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    return row
