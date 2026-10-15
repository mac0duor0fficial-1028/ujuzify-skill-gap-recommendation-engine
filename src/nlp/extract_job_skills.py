"""Phase 4 pipeline step: staged postings -> job_skills.csv.

    python src/nlp/extract_job_skills.py

Runs the rule-based extractor (``rule_extractor_v1``) over ``title + description`` of
every posting in ``data/interim/staging_jobs.csv`` and writes one row per (posting, skill)
to ``data/processed/job_skills.csv`` (git-ignored). Postings with no recognised skill get
no rows. Each row keeps its evidence text and the rule that accepted it, so a recommendation
built on it can be traced back to the words in the posting.

``importance`` is a heuristic from explicit wording ("a plus" = 0.5, otherwise 1.0); it is
not a measured weight. Standard library only.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etl.common import (  # noqa: E402
    JOB_SKILL_COLUMNS, JOB_SKILLS_CSV, STAGING_COLUMNS, STAGING_CSV, EtlError, read_csv, write_csv,
)
from src.nlp.skill_extractor import EXTRACTOR_METHOD, SkillExtractor  # noqa: E402


def build(staging_rows: list[dict], extractor: SkillExtractor | None = None) -> list[dict]:
    extractor = extractor or SkillExtractor()
    out: list[dict] = []
    for r in sorted(staging_rows, key=lambda r: r["job_id"]):
        for e in extractor.extract(f"{r['title']}. {r['description']}"):
            out.append({
                "job_id": r["job_id"], "skill_id": e.skill_id, "canonical_name": e.canonical_name,
                "importance": e.importance, "requirement_level": e.requirement_level,
                "evidence": e.evidence, "rule": e.rule, "mentions": e.mentions,
                "method": EXTRACTOR_METHOD, "is_synthetic": r["is_synthetic"],
            })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract skills from staged job postings.")
    ap.add_argument("--staging", default=str(STAGING_CSV))
    ap.add_argument("--out", default=str(JOB_SKILLS_CSV))
    args = ap.parse_args(argv)
    try:
        staging = read_csv(Path(args.staging), STAGING_COLUMNS)
        if not staging:
            raise EtlError("staging file has no rows")
        rows = build(staging)
        write_csv(Path(args.out), JOB_SKILL_COLUMNS, rows)
    except (EtlError, ValueError, OSError) as e:
        print(f"SKILL EXTRACTION FAILED: {e}")
        return 1
    with_skills = len({r["job_id"] for r in rows})
    levels = Counter(r["requirement_level"] for r in rows)
    print("SKILL EXTRACTION COMPLETE")
    print(f"  method: {EXTRACTOR_METHOD}")
    print(f"  postings: {len(staging)}  with at least one skill: {with_skills}  rows: {len(rows)}")
    print(f"  requirement levels: {dict(sorted(levels.items()))}")
    if any(r["is_synthetic"] == "true" for r in staging):
        print("  NOTE: includes synthetic postings: pipeline test data, not market evidence.")
    print("  NOTE: extractor accuracy is only checked on small author-written gold sets "
          "(python src/evaluation/skill_extraction_eval.py).")
    print(f"  output: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
