"""Data-quality checks for the normalised job dataset (project report, Section 2.7)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from src import config

REQUIRED = ["source_id", "source_job_id", "title", "retrieved_at", "raw_payload_path", "content_hash"]


def validate(path: Path | None = None) -> dict:
    path = Path(path or config.RAW_DIR / "jobs_normalized.csv")
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run src/ingestion/job_api.py first.")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    errors: list[str] = []
    missing_req = {k: 0 for k in REQUIRED}
    non_ke = missing_desc = 0
    ids: set[tuple[str, str]] = set()
    hashes: set[str] = set()
    dup_id = dup_hash = 0
    for r in rows:
        for k in REQUIRED:
            if not (r.get(k) or "").strip():
                missing_req[k] += 1
        if r.get("country_code") and r["country_code"] != "KE":
            non_ke += 1
        if not (r.get("description_raw") or "").strip():
            missing_desc += 1
        key = (r.get("source_id", ""), r.get("source_job_id", ""))
        dup_id += key in ids
        ids.add(key)
        dup_hash += r.get("content_hash") in hashes
        hashes.add(r.get("content_hash"))
    for k, v in missing_req.items():
        if v:
            errors.append(f"{v} rows missing required field '{k}'")
    return {"rows": len(rows), "non_ke_country": non_ke, "missing_description": missing_desc,
            "duplicate_source_ids": dup_id, "duplicate_content_hashes": dup_hash, "errors": errors, "ok": not errors}


if __name__ == "__main__":
    rep = validate(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    for k, v in rep.items():
        print(f"{k}: {v}")
    sys.exit(0 if rep["ok"] else 1)
