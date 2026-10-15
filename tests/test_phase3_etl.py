"""Phase 3 gate tests: staging, labels, skill demand, SQL schema contract.

Two test functions, kept compact on purpose: README gates are cumulative (10 passing
tests after Phase 5) and Phase 2 already uses five.
"""
from __future__ import annotations

import copy
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.etl import build_labels, build_skill_demand, build_staging
from src.etl.common import (
    DEMAND_COLUMNS, LABEL_COLUMNS, STAGING_COLUMNS, EtlError, read_csv,
)
from src.ingestion import job_api
from src.ingestion.source_registry import DEFAULT_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_etl_pipeline_produces_staging_labels_and_skill_demand(tmp_path):
    raw = tmp_path / "raw"
    job_api.run("sample_fixture", out_dir=raw, now=T0)
    staging, labels, demand = tmp_path / "staging.csv", tmp_path / "labels.csv", tmp_path / "demand.csv"

    assert build_staging.main(["--raw-dir", str(raw), "--out", str(staging)]) == 0
    assert build_labels.main(["--staging", str(staging), "--out", str(labels)]) == 0
    assert build_skill_demand.main(["--staging", str(staging), "--labels", str(labels),
                                    "--out", str(demand), "--min-n", "4"]) == 0

    # Staging: exact contract, one row per posting, derived columns, multi-line text survives CSV.
    srows = _rows(staging)
    assert list(srows[0]) == STAGING_COLUMNS and len(srows) == 28
    assert all(r["is_synthetic"] == "true" and r["repost_count"] == "0" for r in srows)
    assert all(int(r["description_chars"]) == len(r["description"]) for r in srows)
    assert any("\n" in r["description"] for r in srows)
    assert srows[0]["title_norm"] == srows[0]["title"].lower().replace("-", " ")

    # Labels: every posting has a row; all nine careers appear; evidence is recorded.
    lrows = _rows(labels)
    assert list(lrows[0]) == LABEL_COLUMNS and len(lrows) == 28
    assert {r["career_id"] for r in lrows} == {f"CA{i:02d}" for i in range(1, 10)}
    by_job = {r["job_id"]: r for r in lrows}
    assert by_job["sample_fixture:SYN-0001"]["matched_alias"] == "data analyst"
    assert all(r["label_status"] == "labeled" and r["label_method"] == "title_alias" for r in lrows)

    # Skill demand: check one number against an independent regex count over the staging text.
    drows = _rows(demand)
    assert list(drows[0]) == DEMAND_COLUMNS
    assert drows[0]["scope"] == "all"  # corpus-wide scope is listed first
    py_all = next(r for r in drows if r["scope"] == "all" and r["canonical_name"] == "python")
    expected = sum(1 for r in srows if re.search(r"\bpython\b", f"{r['title']}. {r['description']}", re.I))
    assert int(py_all["postings_with_skill"]) == expected > 0
    assert int(py_all["postings_in_scope"]) == 28
    assert float(py_all["share"]) == pytest.approx(expected / 28, abs=1e-4)
    # low_sample flag follows --min-n: 'all' (28) and CA01 (4) are fine, CA02 (3) is not.
    flags = {r["scope"]: r["low_sample"] for r in drows}
    assert flags["all"] == "false" and flags["CA01"] == "false" and flags["CA02"] == "true"
    assert all(int(r["synthetic_postings_in_scope"]) == int(r["postings_in_scope"]) for r in drows)
    assert all(r["method"] == "dictionary_scan_v0" for r in drows)
    assert all(0 < float(r["share"]) <= 1 for r in drows)


def test_etl_edge_cases_and_sql_schema_contract(tmp_path):
    # --- cross-run and cross-source deduplication
    reg = json.loads(DEFAULT_REGISTRY.read_text())
    twin = copy.deepcopy(reg["sources"][0])
    twin["source_id"] = "sample_fixture_b"
    reg["sources"].append(twin)
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(reg))
    raw = tmp_path / "raw"
    job_api.run("sample_fixture", registry_path=reg_path, out_dir=raw, now=T0)
    job_api.run("sample_fixture", registry_path=reg_path, out_dir=raw, now=T0 + timedelta(hours=1))
    rows, stats = build_staging.build(raw, reg_path)
    assert stats["records_read"] == 56 and stats["superseded_job_ids"] == 28 and len(rows) == 28
    assert {r["retrieved_at"] for r in rows} == {"2026-10-14T10:00:00Z"}  # latest retrieval wins
    _, latest_stats = build_staging.build(raw, reg_path, latest_only=True)
    assert latest_stats["records_read"] == 28

    job_api.run("sample_fixture_b", registry_path=reg_path, out_dir=raw, now=T0)
    rows, stats = build_staging.build(raw, reg_path)
    assert stats["duplicate_content_dropped"] == 28 and len(rows) == 28
    assert all(r["repost_count"] == 1 and r["source_id"] == "sample_fixture" for r in rows)

    # --- the synthetic filter, and refusing to stage nothing
    assert build_staging.main(["--raw-dir", str(raw), "--registry", str(reg_path),
                               "--out", str(tmp_path / "s.csv"), "--exclude-synthetic"]) == 1
    assert not (tmp_path / "s.csv").exists()

    # --- invalid raw data stops the run and writes nothing
    bad_dir = tmp_path / "bad"
    res = job_api.run("sample_fixture", out_dir=bad_dir, now=T0)
    lines = res.data_path.read_text().splitlines()
    first = json.loads(lines[0])
    first["title"] = "Edited after hashing"
    lines[0] = json.dumps(first, sort_keys=True)
    res.data_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(EtlError, match="content_hash"):
        build_staging.build(bad_dir)
    assert build_staging.main(["--raw-dir", str(bad_dir), "--out", str(tmp_path / "x.csv")]) == 1
    assert not (tmp_path / "x.csv").exists()
    assert build_staging.main(["--raw-dir", str(tmp_path / "none"), "--out", str(tmp_path / "x.csv")]) == 1

    # --- labels: unknown and ambiguous titles stay unmapped, nothing is guessed
    srow = {"job_id": "j1", "title": "Marketing Manager", "is_synthetic": "false"}
    out = build_labels.build([srow, {**srow, "job_id": "j2", "title": "Full-Stack Developer"}])
    assert (out[0]["label_status"], out[0]["career_id"]) == ("unmapped", "")
    assert (out[1]["label_status"], out[1]["career_id"], out[1]["matched_alias"]) == ("labeled", "CA08", "full stack developer")
    assert build_labels.main(["--staging", str(tmp_path / "missing.csv"), "--out", str(tmp_path / "l.csv")]) == 1

    # --- demand refuses staging rows that have no label row (stale upstream file)
    with pytest.raises(EtlError, match="no label row"):
        build_skill_demand.build([{**srow, "description": "SQL", "is_synthetic": "false"}], [])
    with pytest.raises(EtlError):
        read_csv(tmp_path / "missing.csv", STAGING_COLUMNS)

    # --- database/schema.sql staging tables mirror the CSV column contracts exactly
    ddl = (ROOT / "database" / "schema.sql").read_text()

    def table_columns(name: str) -> list[str]:
        body = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(name)} \((.*?)\n\);", ddl, re.S).group(1)
        return [ln.split()[0] for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("--")]

    assert table_columns("staging.jobs") == STAGING_COLUMNS
    assert table_columns("staging.job_labels") == LABEL_COLUMNS
    assert table_columns("staging.skill_demand") == DEMAND_COLUMNS
