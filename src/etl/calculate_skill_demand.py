"""Empirical skill demand: share of sampled jobs mentioning each canonical skill (sample statistic)."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from src import config
from src.taxonomy import get_taxonomy


def calculate(staging_csv: Path | None = None, skills_csv: Path | None = None, out_csv: Path | None = None) -> int:
    staging_csv = Path(staging_csv or config.PROCESSED_DIR / "jobs_staging.csv")
    skills_csv = Path(skills_csv or config.PROCESSED_DIR / "job_skills_nlp.csv")
    out_csv = Path(out_csv or config.SKILL_DEMAND_CSV)
    for p in (staging_csv, skills_csv):
        if not p.exists():
            raise FileNotFoundError(f"{p} not found. Run the ETL and NLP steps first.")
    total = sum(1 for _ in csv.DictReader(open(staging_csv, newline="", encoding="utf-8")))
    if total == 0:
        raise ValueError("Staging dataset is empty; cannot compute demand.")
    counts: Counter[str] = Counter()
    for r in csv.DictReader(open(skills_csv, newline="", encoding="utf-8")):
        counts[r["skill_id"]] += 1  # one relationship per (job, skill)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["skill_id", "job_count", "total_jobs", "demand_rate"])
        for s in get_taxonomy().skills:
            w.writerow([s, counts.get(s, 0), total, round(counts.get(s, 0) / total, 6)])
    return total


if __name__ == "__main__":
    print("jobs:", calculate())
