"""Validate raw job files against the canonical schema (Phase 2 gate).

Run from the project root after ingestion:

    python src/ingestion/validate_jobs.py                  # latest file per source in data/raw
    python src/ingestion/validate_jobs.py path/to/jobs.jsonl

Exit code 0 = no errors (warnings allowed), 1 = at least one error or no data found.
A JSON report is written to ``data/interim/job_validation_report.json``.

The skill and title coverage numbers are *dictionary lookups*, not Phase 4 extraction
and not accuracy measures. For synthetic sources they say nothing about the market.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # allow `python src/ingestion/validate_jobs.py`
    sys.path.insert(0, str(ROOT))

from src.ingestion.raw_schema import (  # noqa: E402
    COUNTRY_RE, FIELD_NAMES, ISO_UTC_RE, OPTIONAL_TEXT_FIELDS, REQUIRED_FIELDS, SCHEMA_VERSION,
    compute_content_hash,
)
from src.ingestion.source_registry import DEFAULT_REGISTRY, Source, load_registry  # noqa: E402
from src.nlp.skill_dictionary import CareerTaxonomy, SkillDictionary  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
REPORT_PATH = ROOT / "data" / "interim" / "job_validation_report.json"
MIN_DESCRIPTION_CHARS = 20      # below this: error (nothing to extract from)
SHORT_DESCRIPTION_CHARS = 100   # below this: warning
FUTURE_TOLERANCE = timedelta(days=1)
_FILE_RE = re.compile(r"^jobs_(?P<source>[a-z][a-z0-9_]*)_\d{8}T\d{6}Z\.jsonl$")


def _dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def read_jsonl(path: Path, errors: list[str]) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"{path.name}:{n}: invalid JSON")
                continue
            if not isinstance(obj, dict):
                errors.append(f"{path.name}:{n}: record is not a JSON object")
                continue
            records.append(obj)
    return records


def check_record(rec: dict, registry: dict[str, Source]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one record."""
    errors: list[str] = []
    warnings: list[str] = []
    jid = rec.get("job_id", "<no job_id>")

    extra = sorted(set(rec) - set(FIELD_NAMES))
    if extra:
        errors.append(f"{jid}: unknown fields {extra}")
    for f in REQUIRED_FIELDS:
        v = rec.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errors.append(f"{jid}: missing required field '{f}'")
    for f in FIELD_NAMES:
        if f not in rec:
            errors.append(f"{jid}: field '{f}' absent (use null for unknown optional values)")
    if errors:
        return errors, warnings  # structure is broken; deeper checks would just cascade

    for f in ("job_id", "source_id", "source_job_id", "title", "description", "retrieved_at",
              "content_hash", "country", "schema_version", *OPTIONAL_TEXT_FIELDS, "date_posted"):
        v = rec[f]
        if v is not None and not isinstance(v, str):
            errors.append(f"{jid}: '{f}' must be text or null")
    if not isinstance(rec["is_synthetic"], bool):
        errors.append(f"{jid}: 'is_synthetic' must be boolean")
    if errors:
        return errors, warnings

    if rec["schema_version"] != SCHEMA_VERSION:
        errors.append(f"{jid}: schema_version {rec['schema_version']!r} != {SCHEMA_VERSION!r}")
    if rec["job_id"] != f"{rec['source_id']}:{rec['source_job_id']}":
        errors.append(f"{jid}: job_id must equal '<source_id>:<source_job_id>'")
    if not COUNTRY_RE.match(rec["country"]):
        errors.append(f"{jid}: country must be ISO alpha-2")
    src = registry.get(rec["source_id"])
    if src is None:
        errors.append(f"{jid}: source_id '{rec['source_id']}' not in registry")
    elif src.is_synthetic != rec["is_synthetic"]:
        errors.append(f"{jid}: is_synthetic disagrees with the registry")

    if len(rec["description"].strip()) < MIN_DESCRIPTION_CHARS:
        errors.append(f"{jid}: description shorter than {MIN_DESCRIPTION_CHARS} characters")
    elif len(rec["description"]) < SHORT_DESCRIPTION_CHARS:
        warnings.append(f"{jid}: short description ({len(rec['description'])} chars)")
    if "<" in rec["description"] and re.search(r"</?[a-zA-Z][^>]*>", rec["description"]):
        errors.append(f"{jid}: description still contains HTML tags")

    expected = compute_content_hash(rec["title"], rec["company"], rec["location"], rec["description"])
    if rec["content_hash"] != expected:
        errors.append(f"{jid}: content_hash does not match content")

    if not ISO_UTC_RE.match(rec["retrieved_at"]):
        errors.append(f"{jid}: retrieved_at must be UTC ISO 8601 'YYYY-MM-DDTHH:MM:SSZ'")
    if rec["date_posted"] is None:
        warnings.append(f"{jid}: date_posted is missing")
    elif not ISO_UTC_RE.match(rec["date_posted"]):
        errors.append(f"{jid}: date_posted must be UTC ISO 8601 or null")
    elif ISO_UTC_RE.match(rec["retrieved_at"]) and _dt(rec["date_posted"]) > _dt(rec["retrieved_at"]) + FUTURE_TOLERANCE:
        errors.append(f"{jid}: date_posted is after retrieved_at")
    if rec["company"] is None:
        warnings.append(f"{jid}: company is missing")
    return errors, warnings


def validate_records(records: list[dict], registry: dict[str, Source]) -> dict:
    """Validate a corpus. Returns a report dict with 'errors', 'warnings' and 'stats'."""
    errors: list[str] = []
    warnings: list[str] = []
    for rec in records:
        e, w = check_record(rec, registry)
        errors += e
        warnings += w

    ids = Counter(r.get("job_id") for r in records)
    errors += [f"{jid}: duplicate job_id ({n} records)" for jid, n in ids.items() if n > 1]
    hashes = Counter(r.get("content_hash") for r in records)
    dup_content = sum(n - 1 for n in hashes.values() if n > 1)
    if dup_content:
        warnings.append(f"{dup_content} record(s) share content_hash with another record (reposts)")

    skills = SkillDictionary.load()
    careers = CareerTaxonomy.load()
    with_skill = 0
    skill_counts: Counter = Counter()
    career_counts: Counter = Counter()
    unmapped_titles = 0
    for r in records:
        if not isinstance(r.get("description"), str) or not isinstance(r.get("title"), str):
            continue
        found = skills.scan(f"{r['title']}. {r['description']}")
        if found:
            with_skill += 1
        skill_counts.update(m.canonical_name for m in found)
        c = careers.match_title(r["title"])
        if c:
            career_counts[c.career_id] += 1
        else:
            unmapped_titles += 1
    n = len(records)
    stats = {
        "records": n,
        "by_source": dict(Counter(r.get("source_id") for r in records)),
        "synthetic_records": sum(1 for r in records if r.get("is_synthetic") is True),
        "with_at_least_one_dictionary_skill": with_skill,
        "title_mapped_to_career": n - unmapped_titles,
        "title_unmapped": unmapped_titles,
        "title_career_counts": dict(sorted(career_counts.items())),
        "top_dictionary_skills": skill_counts.most_common(10),
    }
    for r in records:
        if isinstance(r.get("description"), str) and not skills.scan(r["description"]):
            warnings.append(f"{r.get('job_id')}: no dictionary skill found in description")
    return {"errors": errors, "warnings": warnings, "stats": stats}


def latest_files(raw_dir: Path) -> list[Path]:
    """Newest data file per source_id in raw_dir."""
    best: dict[str, Path] = {}
    for p in sorted(raw_dir.glob("jobs_*.jsonl")):
        m = _FILE_RE.match(p.name)
        if m:
            best[m["source"]] = p  # sorted by name => timestamp order
    return list(best.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate raw job JSONL files.")
    ap.add_argument("paths", nargs="*", help="JSONL files (default: latest per source in data/raw)")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    ap.add_argument("--report", default=str(REPORT_PATH))
    args = ap.parse_args(argv)

    files = [Path(p) for p in args.paths] or latest_files(Path(args.raw_dir))
    if not files:
        print("JOB VALIDATION FAILED")
        print("  - no raw job files found; run: python src/ingestion/job_api.py")
        return 1
    registry = load_registry(args.registry)

    read_errors: list[str] = []
    records: list[dict] = []
    for f in files:
        records += read_jsonl(f, read_errors)
    report = validate_records(records, registry)
    report["errors"] = read_errors + report["errors"]
    report["files"] = [f.name for f in files]

    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2), encoding="utf-8")

    st = report["stats"]
    if report["errors"]:
        print("JOB VALIDATION FAILED")
        for e in report["errors"][:50]:
            print(f"  - {e}")
        if len(report["errors"]) > 50:
            print(f"  ... and {len(report['errors']) - 50} more (see {rp})")
        return 1

    print("JOB VALIDATION PASSED")
    print(f"  files:    {', '.join(report['files'])}")
    print(f"  records:  {st['records']}  ({len(report['warnings'])} warnings)")
    if st["synthetic_records"]:
        print(f"  NOTE: {st['synthetic_records']} synthetic record(s). Pipeline test data, not market evidence.")
    print(f"  dictionary skills found in {st['with_at_least_one_dictionary_skill']}/{st['records']} postings"
          " (lookup coverage, not extraction accuracy)")
    print(f"  titles mapped to a career: {st['title_mapped_to_career']}/{st['records']}")
    print(f"  report:   {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
