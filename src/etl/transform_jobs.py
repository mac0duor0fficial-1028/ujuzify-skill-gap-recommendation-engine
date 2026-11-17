"""Clean, normalise (Kenya only) and de-duplicate jobs into the staging dataset."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from src import config
from src.ingestion.job_api import content_hash


def clean_text(value: str | None) -> str:
    """Collapse runs of whitespace to a single space (never remove it)."""
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_company(value: str | None) -> str:
    return clean_text(re.sub(r"\b(ltd|limited|inc|llc|plc)\.?$", "", clean_text(value), flags=re.I)).strip(" ,.")


def normalize_country(value: str | None) -> str:
    v = clean_text(value).upper()
    return "KE" if v in {"KE", "KEN", "KENYA"} else v


def transform(in_csv: Path | None = None, out_csv: Path | None = None) -> dict:
    in_csv = Path(in_csv or config.RAW_DIR / "jobs_normalized.csv")
    out_csv = Path(out_csv or config.PROCESSED_DIR / "jobs_staging.csv")
    if not in_csv.exists():
        raise FileNotFoundError(f"{in_csv} not found. Run the ingestion step first.")
    with open(in_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    seen_ids, seen_hash, out = set(), set(), []
    stats = {"input": len(rows), "dropped_non_kenya": 0, "dropped_duplicate_id": 0, "dropped_duplicate_hash": 0}
    for r in rows:
        r["title"], r["company_name"] = clean_text(r.get("title")), normalize_company(r.get("company_name"))
        r["description_raw"] = clean_text(r.get("description_raw"))
        r["location_raw"] = clean_text(r.get("location_raw"))
        r["content_hash"] = content_hash(r["title"], r["company_name"], r["description_raw"])  # hash on cleaned fields
        r["country_code"] = normalize_country(r.get("country_code")) or ("KE" if re.search(r"kenya|nairobi|mombasa|kisumu|nakuru", r.get("location_raw", ""), re.I) else "")
        if r["country_code"] != "KE":
            stats["dropped_non_kenya"] += 1
            continue
        key = (r["source_id"], r["source_job_id"])
        if key in seen_ids:
            stats["dropped_duplicate_id"] += 1
            continue
        if r["content_hash"] in seen_hash:
            stats["dropped_duplicate_hash"] += 1
            continue
        seen_ids.add(key)
        seen_hash.add(r["content_hash"])
        r["job_id"] = f"{r['source_id']}:{r['source_job_id']}"
        out.append(r)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ["job_id"] + [k for k in (rows[0].keys() if rows else [])]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    stats["output"] = len(out)
    return stats


if __name__ == "__main__":
    print(transform())
