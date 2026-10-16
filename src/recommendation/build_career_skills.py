"""Phase 5 step: build career skill profiles -> data/processed/career_skills.csv.

    python src/recommendation/build_career_skills.py [--min-postings 30]

Joins staging postings, weak career labels (Phase 3) and extracted skills (Phase 4) and
writes one row per (career, skill) with ``frequency`` and ``importance`` (see
profiles.py). This is the editable-data form of the career profiles that the engine reads;
nothing about careers is hard-coded in the engine (explainability rule 7).

Caveats: labels are weak title-derived labels, skills come from a rule extractor, and
small corpora give lumpy shares. ``low_sample`` marks careers below ``--min-postings``.
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etl.common import (  # noqa: E402
    CAREER_SKILL_COLUMNS, CAREER_SKILLS_CSV, JOB_SKILL_COLUMNS, JOB_SKILLS_CSV, LABEL_COLUMNS,
    LABELS_CSV, STAGING_COLUMNS, STAGING_CSV, EtlError, read_csv, write_csv,
)
from src.nlp.skill_dictionary import CAREERS_CSV, SkillDictionary  # noqa: E402
from src.nlp.skill_extractor import EXTRACTOR_METHOD  # noqa: E402
from src.recommendation.profiles import DEFAULT_MIN_POSTINGS, CorpusCounts, postings_from_rows  # noqa: E402


def career_names(path: Path = CAREERS_CSV) -> dict[str, str]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return {r["career_id"]: r["career_name"] for r in csv.DictReader(fh)}


def build(staging_rows: list[dict], label_rows: list[dict], job_skills_rows: list[dict],
          min_postings: int = DEFAULT_MIN_POSTINGS, skills: SkillDictionary | None = None) -> list[dict]:
    skills = skills or SkillDictionary.load()
    staged = {r["job_id"] for r in staging_rows}
    stale = [js["job_id"] for js in job_skills_rows if js["job_id"] not in staged]
    if stale:
        raise EtlError(f"job_skills.csv refers to {stale[0]}, which is not in staging; "
                       "rerun extract_job_skills.py after build_staging.py")
    postings = postings_from_rows(staging_rows, label_rows, job_skills_rows)
    if not postings:
        raise EtlError("no labeled postings; nothing to build career profiles from")
    counts = CorpusCounts(postings, career_names(), min_postings)
    rows: list[dict] = []
    for cid, prof in counts.profiles().items():
        for sid, st in sorted(prof.skills.items(), key=lambda kv: (-kv[1].importance, kv[0])):
            sk = skills.skills[sid]
            rows.append({
                "career_id": cid, "career_name": prof.career_name, "skill_id": sid,
                "canonical_name": sk.canonical_name, "category": sk.category,
                "postings_with_skill": st.count, "postings_in_career": prof.postings,
                "frequency": f"{st.frequency:.4f}", "importance": f"{st.importance:.4f}",
                "low_sample": prof.low_sample, "synthetic_postings_in_career": prof.synthetic_postings,
                "method": f"job_skills:{EXTRACTOR_METHOD}",
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build career skill profiles.")
    ap.add_argument("--staging", default=str(STAGING_CSV))
    ap.add_argument("--labels", default=str(LABELS_CSV))
    ap.add_argument("--job-skills", default=str(JOB_SKILLS_CSV))
    ap.add_argument("--out", default=str(CAREER_SKILLS_CSV))
    ap.add_argument("--min-postings", type=int, default=DEFAULT_MIN_POSTINGS,
                    help="careers with fewer postings are flagged low_sample")
    args = ap.parse_args(argv)
    try:
        rows = build(read_csv(Path(args.staging), STAGING_COLUMNS),
                     read_csv(Path(args.labels), LABEL_COLUMNS),
                     read_csv(Path(args.job_skills), JOB_SKILL_COLUMNS), args.min_postings)
        write_csv(Path(args.out), CAREER_SKILL_COLUMNS, rows)
    except (EtlError, ValueError, OSError) as e:
        print(f"CAREER PROFILES FAILED: {e}")
        return 1
    per = {}
    for r in rows:
        per[r["career_id"]] = (r["career_name"], int(r["postings_in_career"]), r["low_sample"])
    print("CAREER PROFILES COMPLETE")
    print(f"  careers: {len(per)}  rows: {len(rows)}")
    for cid, (name, n, low) in sorted(per.items()):
        print(f"    {cid} {name}: {n} postings" + ("  (low sample)" if low == "true" else ""))
    print("  NOTE: derived from weak title labels and extracted skills; sample statistics only.")
    print(f"  output: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
