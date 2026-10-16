"""Shared paths, column contracts and CSV helpers for the Phase 3 ETL scripts.

Standard library only.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
STAGING_CSV = ROOT / "data" / "interim" / "staging_jobs.csv"
LABELS_CSV = ROOT / "data" / "processed" / "job_labels.csv"
DEMAND_CSV = ROOT / "data" / "processed" / "skill_demand.csv"
JOB_SKILLS_CSV = ROOT / "data" / "processed" / "job_skills.csv"
CAREER_SKILLS_CSV = ROOT / "data" / "processed" / "career_skills.csv"
RECOMMENDATIONS_CSV = ROOT / "data" / "processed" / "recommendations.csv"

# Column contracts. database/schema.sql mirrors these (a test keeps them in sync).
STAGING_COLUMNS = [
    "job_id", "source_id", "source_job_id", "source_url", "title", "title_norm", "company",
    "location", "country", "description", "description_chars", "employment_type",
    "experience_required", "education_required", "date_posted", "retrieved_at",
    "content_hash", "is_synthetic", "repost_count",
]
LABEL_COLUMNS = [
    "job_id", "career_id", "career_name", "label_status", "label_method", "matched_alias",
    "is_synthetic",
]
JOB_SKILL_COLUMNS = [
    "job_id", "skill_id", "canonical_name", "importance", "requirement_level", "evidence",
    "rule", "mentions", "method", "is_synthetic",
]
CAREER_SKILL_COLUMNS = [
    "career_id", "career_name", "skill_id", "canonical_name", "category", "postings_with_skill",
    "postings_in_career", "frequency", "importance", "low_sample",
    "synthetic_postings_in_career", "method",
]
RECOMMENDATION_COLUMNS = [
    "recommendation_id", "profile_id", "career_id", "rank", "score", "model_version", "created_at",
    "matched_skills", "missing_skills", "postings_in_career", "low_sample",
]
DEMAND_COLUMNS = [
    "scope", "scope_name", "skill_id", "canonical_name", "category", "postings_with_skill",
    "postings_in_scope", "share", "low_sample", "synthetic_postings_in_scope", "method",
]


class EtlError(RuntimeError):
    """An ETL step cannot proceed (bad input, missing upstream file)."""


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Atomic CSV write. None becomes an empty cell, which PostgreSQL's COPY reads as NULL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r.get(c)) for c in columns})
    tmp.replace(path)


def read_csv(path: Path, columns: list[str]) -> list[dict]:
    """Read a CSV written by write_csv, checking the header matches the contract."""
    if not path.exists():
        raise EtlError(f"missing input {path}; run the upstream ETL step first")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != columns:
            raise EtlError(f"{path.name}: expected columns {columns}, found {reader.fieldnames}")
        return list(reader)
