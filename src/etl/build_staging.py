"""ETL step 1: raw JSONL -> staging CSV (Phase 3).

    python src/etl/build_staging.py [--latest-only] [--exclude-synthetic]

Reads ``data/raw/jobs_*.jsonl``, re-validates each file with the Phase 2 validator
(any error stops the run, nothing is written), then:

* keeps the most recently retrieved version of each ``job_id`` (cross-run dedup),
* folds postings with identical ``content_hash`` into one representative, the one
  with the earliest ``date_posted`` (cross-source / repost dedup), and records how many
  were folded in ``repost_count``,
* adds ``title_norm`` and ``description_chars``.

Output: ``data/interim/staging_jobs.csv`` (git-ignored). Standard library only.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # allow `python src/etl/build_staging.py`
    sys.path.insert(0, str(ROOT))

from src.etl.common import RAW_DIR, STAGING_COLUMNS, STAGING_CSV, EtlError, write_csv  # noqa: E402
from src.ingestion.source_registry import DEFAULT_REGISTRY, load_registry  # noqa: E402
from src.ingestion.validate_jobs import _FILE_RE, latest_files, read_jsonl, validate_records  # noqa: E402
from src.nlp.skill_dictionary import normalize  # noqa: E402


def raw_files(raw_dir: Path, latest_only: bool) -> list[Path]:
    if latest_only:
        return latest_files(raw_dir)
    return sorted(p for p in raw_dir.glob("jobs_*.jsonl") if _FILE_RE.match(p.name))


def build(raw_dir: Path = RAW_DIR, registry_path: Path | str | None = None,
          latest_only: bool = False, exclude_synthetic: bool = False) -> tuple[list[dict], dict]:
    """Return (staging rows, stats). Raises EtlError if there is no input or it is invalid."""
    files = raw_files(Path(raw_dir), latest_only)
    if not files:
        raise EtlError("no raw job files found; run: python src/ingestion/job_api.py")
    registry = load_registry(registry_path or DEFAULT_REGISTRY)

    records: list[dict] = []
    problems: list[str] = []
    for f in files:
        read_errors: list[str] = []
        recs = read_jsonl(f, read_errors)
        report = validate_records(recs, registry)
        problems += [f"{f.name}: {e}" for e in read_errors + report["errors"]]
        records += recs
    if problems:
        shown = "\n  - ".join(problems[:20])
        raise EtlError(f"raw data failed validation ({len(problems)} error(s)):\n  - {shown}")

    stats: Counter = Counter(files=len(files), records_read=len(records))

    # 1. latest retrieval wins per job_id. Python's sort is stable, so on equal timestamps
    #    the record from the later file (read later) wins.
    latest: dict[str, dict] = {}
    for r in sorted(records, key=lambda r: r["retrieved_at"]):
        latest[r["job_id"]] = r
    stats["superseded_job_ids"] = len(records) - len(latest)

    if exclude_synthetic:
        kept = {k: v for k, v in latest.items() if not v["is_synthetic"]}
        stats["excluded_synthetic"] = len(latest) - len(kept)
        latest = kept

    # 2. identical content collapses to one representative (earliest posted, then job_id).
    groups: dict[str, list[dict]] = {}
    for r in latest.values():
        groups.setdefault(r["content_hash"], []).append(r)
    rows: list[dict] = []
    for group in groups.values():
        group.sort(key=lambda r: (r["date_posted"] is None, r["date_posted"] or "", r["job_id"]))
        rep = dict(group[0])
        rep["repost_count"] = len(group) - 1
        stats["duplicate_content_dropped"] += len(group) - 1
        rep["title_norm"] = normalize(rep["title"])
        rep["description_chars"] = len(rep["description"])
        rows.append(rep)
    rows.sort(key=lambda r: r["job_id"])
    stats["written"] = len(rows)
    stats["synthetic_written"] = sum(1 for r in rows if r["is_synthetic"])
    return rows, dict(stats)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the staging jobs CSV from raw JSONL.")
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    ap.add_argument("--out", default=str(STAGING_CSV))
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--latest-only", action="store_true", help="use only the newest file per source")
    ap.add_argument("--exclude-synthetic", action="store_true")
    args = ap.parse_args(argv)
    try:
        rows, stats = build(Path(args.raw_dir), args.registry, args.latest_only, args.exclude_synthetic)
        if not rows:
            raise EtlError("no postings left after filtering; nothing to stage")
        write_csv(Path(args.out), STAGING_COLUMNS, rows)
    except (EtlError, ValueError, OSError) as e:
        print(f"STAGING FAILED: {e}")
        return 1
    print("STAGING COMPLETE")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    if stats["synthetic_written"]:
        print(f"  NOTE: {stats['synthetic_written']} synthetic posting(s): pipeline test data, not market evidence.")
    print(f"  output: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
