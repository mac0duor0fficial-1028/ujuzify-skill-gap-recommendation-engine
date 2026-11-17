"""Conservative title-keyword baseline that assigns candidate career ids to job postings.

Deliberately not the final classifier: ambiguous titles remain unlabelled.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src import config
from src.taxonomy import get_taxonomy


def label(staging_csv: Path | None = None, out_csv: Path | None = None) -> tuple[int, int]:
    staging_csv = Path(staging_csv or config.PROCESSED_DIR / "jobs_staging.csv")
    out_csv = Path(out_csv or config.PROCESSED_DIR / "job_career_labels.csv")
    if not staging_csv.exists():
        raise FileNotFoundError(f"{staging_csv} not found. Run src/etl/transform_jobs.py first.")
    tax = get_taxonomy()
    labelled = total = 0
    with open(staging_csv, newline="", encoding="utf-8") as f, open(out_csv, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(["job_id", "title", "candidate_career_id", "method"])
        for r in csv.DictReader(f):
            total += 1
            c = tax.match_career_title(r["title"])
            if c:
                labelled += 1
            w.writerow([r["job_id"], r["title"], c.career_id if c else "", "title_keyword_baseline"])
    return labelled, total


if __name__ == "__main__":
    print("labelled/total:", label())
