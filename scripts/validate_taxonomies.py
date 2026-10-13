"""Validate the career and skill taxonomies (Phase 1 gate).

Plain script (not a pytest test, so the 10/13/15 test gates are unaffected).
Standard library only. Run from the project root:

    python scripts/validate_taxonomies.py

Exit code 0 = all checks passed, 1 = at least one check failed.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAREERS_CSV = ROOT / "data" / "external" / "career_taxonomy.csv"
SKILLS_CSV = ROOT / "data" / "external" / "skill_taxonomy.csv"

EXPECTED_CAREERS = 9
EXPECTED_SKILLS = 70
EXPECTED_CAREER_IDS = [f"CA{i:02d}" for i in range(1, EXPECTED_CAREERS + 1)]
CAREER_COLUMNS = ["career_id", "career_name", "career_family", "description"]
SKILL_COLUMNS = ["skill_id", "canonical_name", "display_name", "category", "aliases"]
SKILL_ID_RE = re.compile(r"^SK\d{2}$")
CANONICAL_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def normalize(text: str) -> str:
    """Same normalization rules planned for the Phase 4 extractor."""
    t = text.lower().replace("&", " and ")
    t = re.sub(r"[-/_]", " ", t)
    t = re.sub(r"[^a-z0-9+#.\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def read_rows(path: Path, columns: list[str], errors: list[str]) -> list[dict]:
    if not path.exists():
        errors.append(f"Missing file: {path.relative_to(ROOT)}")
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != columns:
            errors.append(f"{path.name}: expected columns {columns}, found {reader.fieldnames}")
            return []
        return list(reader)


def check_careers(rows: list[dict], errors: list[str]) -> None:
    if len(rows) != EXPECTED_CAREERS:
        errors.append(f"Careers: expected {EXPECTED_CAREERS}, found {len(rows)}")
    ids = [r["career_id"] for r in rows]
    if ids != EXPECTED_CAREER_IDS:
        errors.append(f"Career IDs must be {EXPECTED_CAREER_IDS[0]}..{EXPECTED_CAREER_IDS[-1]} in order; found {ids}")
    if len(set(ids)) != len(ids):
        errors.append("Careers: duplicate career_id values")
    names = [r["career_name"].strip().lower() for r in rows]
    if len(set(names)) != len(names):
        errors.append("Careers: duplicate career_name values")
    for r in rows:
        for col in CAREER_COLUMNS:
            if not r[col].strip():
                errors.append(f"Careers: empty {col} for {r['career_id'] or '?'}")


def check_skills(rows: list[dict], errors: list[str]) -> None:
    if len(rows) != EXPECTED_SKILLS:
        errors.append(f"Skills: expected {EXPECTED_SKILLS}, found {len(rows)}")
    ids = [r["skill_id"] for r in rows]
    if len(set(ids)) != len(ids):
        errors.append("Skills: duplicate skill_id values")
    canon = [r["canonical_name"] for r in rows]
    if len(set(canon)) != len(canon):
        errors.append("Skills: duplicate canonical_name values")

    owners: dict[str, set[str]] = defaultdict(set)  # normalized surface form -> skill_ids
    for r in rows:
        sid = r["skill_id"]
        if not SKILL_ID_RE.match(sid):
            errors.append(f"{sid}: skill_id must match SKnn")
        if not CANONICAL_RE.match(r["canonical_name"]):
            errors.append(f"{sid}: canonical_name '{r['canonical_name']}' must be lowercase snake_case")
        for col in ("display_name", "category"):
            if not r[col].strip():
                errors.append(f"{sid}: empty {col}")
        forms = {normalize(r["canonical_name"]), normalize(r["display_name"])}
        forms.update(normalize(a) for a in r["aliases"].split("|") if a.strip())
        forms.discard("")
        for form in forms:
            owners[form].add(sid)

    for form, sids in sorted(owners.items()):
        if len(sids) > 1:
            errors.append(f"Alias collision: '{form}' maps to {sorted(sids)}")


def main() -> int:
    errors: list[str] = []
    careers = read_rows(CAREERS_CSV, CAREER_COLUMNS, errors)
    skills = read_rows(SKILLS_CSV, SKILL_COLUMNS, errors)
    if careers:
        check_careers(careers, errors)
    if skills:
        check_skills(skills, errors)

    if errors:
        print("TAXONOMY VALIDATION FAILED")
        for e in errors:
            print(f"  - {e}")
        return 1

    cats = defaultdict(int)
    for r in skills:
        cats[r["category"]] += 1
    n_aliases = sum(len([a for a in r["aliases"].split("|") if a.strip()]) for r in skills)
    print("TAXONOMY VALIDATION PASSED")
    print(f"  careers: {len(careers)}")
    print(f"  skills:  {len(skills)} ({n_aliases} aliases, no collisions)")
    for c, n in sorted(cats.items()):
        print(f"    {c}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
