"""Run the hybrid extractor over staging jobs -> data/processed/job_skills_nlp.csv."""
from __future__ import annotations

import csv
from pathlib import Path

from src import config
from src.nlp.hybrid_skill_extractor import SkillExtractor


def run(staging_csv: Path | None = None, out_csv: Path | None = None, extractor: SkillExtractor | None = None) -> int:
    staging_csv = Path(staging_csv or config.PROCESSED_DIR / "jobs_staging.csv")
    out_csv = Path(out_csv or config.PROCESSED_DIR / "job_skills_nlp.csv")
    if not config.SKILL_TAXONOMY_CSV.exists():
        raise FileNotFoundError(f"Taxonomy missing: {config.SKILL_TAXONOMY_CSV}")
    if not staging_csv.exists():
        raise FileNotFoundError(f"{staging_csv} not found. Run src/etl/transform_jobs.py first; "
                                "refusing to fabricate training data.")
    ext = extractor or SkillExtractor()
    n = 0
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(staging_csv, newline="", encoding="utf-8") as f, open(out_csv, "w", newline="", encoding="utf-8") as g:
        w = csv.DictWriter(g, fieldnames=["job_id", "skill_id", "canonical_name", "category", "matched_text", "context", "extraction_method", "confidence"])
        w.writeheader()
        for job in csv.DictReader(f):
            for m in ext.extract(f"{job.get('title','')}. {job.get('description_raw','')}"):
                w.writerow({"job_id": job["job_id"], "skill_id": m.skill_id, "canonical_name": m.canonical_name,
                            "category": m.category, "matched_text": m.matched_text, "context": m.context,
                            "extraction_method": m.extraction_method, "confidence": m.confidence})
                n += 1
    return n


if __name__ == "__main__":
    print("extracted", run(), "job-skill relationships")
