"""ETL step 3: staging + labels -> skill demand table (Phase 3).

    python src/etl/build_skill_demand.py [--min-n 30] [--job-skills data/processed/job_skills.csv]

For the whole corpus (scope ``all``) and for each career with labeled postings, counts
the postings mentioning each skill: ``share = postings_with_skill / postings_in_scope``.
Only skills with at least one posting get a row.

CAVEATS (docs/explainability_requirements.md rule 3):
* By default skills come from the Phase 2 dictionary scan (``method=dictionary_scan_v0``),
  a provisional method. With ``--job-skills data/processed/job_skills.csv`` the Phase 4
  rule extractor's output is used instead (``method=rule_extractor_v1``).
* Shares are sample statistics from the acquired corpus, not a ranking of the Kenyan
  labour market. ``low_sample=true`` marks scopes with fewer than ``--min-n`` postings,
  and ``synthetic_postings_in_scope`` shows how many came from fixtures.

Output: ``data/processed/skill_demand.csv`` (git-ignored). Standard library only.
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
    DEMAND_COLUMNS, DEMAND_CSV, JOB_SKILL_COLUMNS, LABEL_COLUMNS, LABELS_CSV, STAGING_COLUMNS,
    STAGING_CSV, EtlError, read_csv, write_csv,
)
from src.nlp.skill_dictionary import SkillDictionary  # noqa: E402
from src.nlp.skill_extractor import EXTRACTOR_METHOD  # noqa: E402

METHOD = "dictionary_scan_v0"
DEFAULT_MIN_N = 30


def build(staging_rows: list[dict], label_rows: list[dict], min_n: int = DEFAULT_MIN_N,
          skills: SkillDictionary | None = None,
          job_skills_rows: list[dict] | None = None) -> list[dict]:
    """Skill demand per scope.

    By default skills come from the provisional dictionary scan (method dictionary_scan_v0).
    If ``job_skills_rows`` (the Phase 4 job_skills.csv) is given, those extractions are used
    instead (method rule_extractor_v1) and must refer only to postings present in staging.
    """
    skills = skills or SkillDictionary.load()
    method = METHOD
    extracted: dict[str, set[str]] | None = None
    if job_skills_rows is not None:
        method = EXTRACTOR_METHOD
        staged_ids = {r["job_id"] for r in staging_rows}
        extracted = {}
        for js in job_skills_rows:
            if js["method"] != EXTRACTOR_METHOD:
                raise EtlError(f"job_skills row for {js['job_id']} has method {js['method']!r}, "
                               f"expected {EXTRACTOR_METHOD!r}")
            if js["job_id"] not in staged_ids:
                raise EtlError(f"job_skills.csv refers to {js['job_id']}, which is not in staging; "
                               "rerun extract_job_skills.py after build_staging.py")
            extracted.setdefault(js["job_id"], set()).add(js["skill_id"])
    label_by_job = {l["job_id"]: l for l in label_rows}
    missing = [r["job_id"] for r in staging_rows if r["job_id"] not in label_by_job]
    if missing:
        raise EtlError(f"{len(missing)} staged posting(s) have no label row (e.g. {missing[0]}); "
                       "rerun build_labels.py after build_staging.py")

    # scope -> (name, n_postings, n_synthetic, Counter of skill_id)
    scopes: dict[str, dict] = {}

    def scope(key: str, name: str) -> dict:
        return scopes.setdefault(key, {"name": name, "n": 0, "syn": 0, "skills": Counter()})

    for r in staging_rows:
        if extracted is not None:
            found = extracted.get(r["job_id"], set())
        else:
            found = set(skills.skill_ids_in(f"{r['title']}. {r['description']}"))
        label = label_by_job[r["job_id"]]
        targets = [("all", "All postings")]
        if label["career_id"]:
            targets.append((label["career_id"], label["career_name"]))
        for key, name in targets:
            s = scope(key, name)
            s["n"] += 1
            s["syn"] += r["is_synthetic"] == "true"
            s["skills"].update(found)

    rows: list[dict] = []
    for key in sorted(scopes, key=lambda k: (k != "all", k)):
        s = scopes[key]
        ranked = sorted(s["skills"].items(), key=lambda kv: (-kv[1], kv[0]))
        for sid, count in ranked:
            sk = skills.skills[sid]
            rows.append({
                "scope": key, "scope_name": s["name"], "skill_id": sid,
                "canonical_name": sk.canonical_name, "category": sk.category,
                "postings_with_skill": count, "postings_in_scope": s["n"],
                "share": f"{count / s['n']:.4f}", "low_sample": s["n"] < min_n,
                "synthetic_postings_in_scope": s["syn"], "method": method,
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the skill demand table.")
    ap.add_argument("--staging", default=str(STAGING_CSV))
    ap.add_argument("--labels", default=str(LABELS_CSV))
    ap.add_argument("--job-skills", default=None,
                    help="job_skills.csv from extract_job_skills.py; use extractor output instead of "
                         "the provisional dictionary scan")
    ap.add_argument("--out", default=str(DEMAND_CSV))
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N,
                    help="scopes with fewer postings are flagged low_sample")
    args = ap.parse_args(argv)
    try:
        staging = read_csv(Path(args.staging), STAGING_COLUMNS)
        labels = read_csv(Path(args.labels), LABEL_COLUMNS)
        if not staging:
            raise EtlError("staging file has no rows")
        js_rows = read_csv(Path(args.job_skills), JOB_SKILL_COLUMNS) if args.job_skills else None
        rows = build(staging, labels, args.min_n, job_skills_rows=js_rows)
        write_csv(Path(args.out), DEMAND_COLUMNS, rows)
    except (EtlError, ValueError, OSError) as e:
        print(f"SKILL DEMAND FAILED: {e}")
        return 1
    scopes = {r["scope"]: (r["postings_in_scope"], r["low_sample"]) for r in rows}
    print("SKILL DEMAND COMPLETE")
    print(f"  rows: {len(rows)}  scopes: {len(scopes)}")
    low = sorted(k for k, (_, lo) in scopes.items() if lo)
    if low:
        print(f"  low_sample scopes (n < {args.min_n}): {', '.join(low)}")
    print(f"  method: {rows[0]['method'] if rows else METHOD}")
    print("  NOTE: sample statistics from the acquired corpus, not a ranking of the Kenyan labour market.")
    print(f"  output: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
