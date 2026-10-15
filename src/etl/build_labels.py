"""ETL step 2: staging CSV -> weak career labels (Phase 3).

    python src/etl/build_labels.py

Assigns each posting one of the nine careers when its *title* contains a known title
alias (data/external/career_title_aliases.csv). Postings with no match, or a tie
between careers, are kept with ``label_status=unmapped`` and no career: nothing is
guessed.

CAVEAT: these are weak, rule-based labels derived from titles, not reviewed gold
labels. Never use them to evaluate a model that also reads the title (circular).
Output: ``data/processed/job_labels.csv`` (git-ignored). Standard library only.
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
    LABEL_COLUMNS, LABELS_CSV, STAGING_COLUMNS, STAGING_CSV, EtlError, read_csv, write_csv,
)
from src.nlp.skill_dictionary import CareerTaxonomy  # noqa: E402

METHOD = "title_alias"


def build(staging_rows: list[dict], careers: CareerTaxonomy | None = None) -> list[dict]:
    careers = careers or CareerTaxonomy.load()
    out: list[dict] = []
    for r in staging_rows:
        hit = careers.match_title_with_alias(r["title"])
        if hit:
            career, alias = hit
            out.append({"job_id": r["job_id"], "career_id": career.career_id,
                        "career_name": career.career_name, "label_status": "labeled",
                        "label_method": METHOD, "matched_alias": alias,
                        "is_synthetic": r["is_synthetic"]})
        else:
            out.append({"job_id": r["job_id"], "career_id": "", "career_name": "",
                        "label_status": "unmapped", "label_method": METHOD, "matched_alias": "",
                        "is_synthetic": r["is_synthetic"]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build weak career labels from job titles.")
    ap.add_argument("--staging", default=str(STAGING_CSV))
    ap.add_argument("--out", default=str(LABELS_CSV))
    args = ap.parse_args(argv)
    try:
        staging = read_csv(Path(args.staging), STAGING_COLUMNS)
        if not staging:
            raise EtlError("staging file has no rows")
        labels = build(staging)
        write_csv(Path(args.out), LABEL_COLUMNS, labels)
    except (EtlError, ValueError, OSError) as e:
        print(f"LABELING FAILED: {e}")
        return 1
    counts = Counter(l["career_id"] or "unmapped" for l in labels)
    print("LABELING COMPLETE")
    print(f"  postings: {len(labels)}  labeled: {len(labels) - counts['unmapped']}  unmapped: {counts['unmapped']}")
    for k, v in sorted(counts.items()):
        print(f"    {k}: {v}")
    print("  NOTE: weak labels from title aliases, not reviewed gold labels.")
    print(f"  output: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
