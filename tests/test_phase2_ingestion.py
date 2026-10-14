"""Phase 2 gate tests: registry, schema, ingestion, validation, skill dictionary.

Five test functions, kept compact on purpose: the README gates are cumulative
(10 passing tests after Phase 5), so Phase 2 leaves room for Phases 3-5.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.ingestion import job_api, validate_jobs
from src.ingestion.raw_schema import FIELD_NAMES, build_record, compute_content_hash, parse_datetime
from src.ingestion.source_registry import (
    DEFAULT_REGISTRY, SourceNotAllowed, assert_runnable, load_registry, validate_registry_data,
)
from src.nlp.skill_dictionary import CareerTaxonomy, SkillDictionary, normalize

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)


def test_registry_gates_and_schema_helpers():
    reg = load_registry()
    assert {"sample_fixture", "job_opportunities_api"} <= set(reg)
    assert_runnable(reg["sample_fixture"])
    # The placeholder live source must stay blocked until terms are reviewed.
    with pytest.raises(SourceNotAllowed):
        assert_runnable(reg["job_opportunities_api"], env={"JOB_API_BASE_URL": "https://x", "JOB_API_KEY": "k"})
    # enabled=true without terms_reviewed is a registry error.
    data = json.loads(DEFAULT_REGISTRY.read_text())
    bad = copy.deepcopy(data)
    bad["sources"][1]["enabled"] = True
    assert any("requires terms_reviewed" in e for e in validate_registry_data(bad))
    bad = copy.deepcopy(data)
    del bad["sources"][0]["config"]["field_map"]["title"]
    assert any("must map 'title'" in e for e in validate_registry_data(bad))

    # Schema helpers: HTML stripping, Kenyan DD/MM/YYYY dates, hash stability.
    rec = build_record(source_id="s", source_job_id="1", title=" Data  Analyst ",
                       description="<p>SQL &amp; Excel</p><ul><li>Power BI</li></ul>", country="KE",
                       retrieved_at="2026-10-14T09:00:00Z", is_synthetic=True, date_posted="05/10/2026")
    assert list(rec) == list(FIELD_NAMES)
    assert rec["description"] == "SQL & Excel\nPower BI"
    assert rec["date_posted"] == "2026-10-05T00:00:00Z"
    # Pin the hash recipe independently of the implementation: sha256 over lowercased,
    # whitespace-collapsed title/company/location/description joined by \x1f.
    assert rec["content_hash"] == hashlib.sha256(
        "data analyst\x1f\x1f\x1fsql & excel power bi".encode()).hexdigest()
    assert rec["content_hash"] == compute_content_hash("Data Analyst", None, None, "SQL &  Excel\nPower BI")
    assert parse_datetime("not a date") is None


def test_ingest_fixture_then_validate_passes(tmp_path):
    res = job_api.run("sample_fixture", out_dir=tmp_path, now=NOW)
    c = res.manifest["counts"]
    assert c["fetched"] == 30 and c["written"] == 28
    assert c["duplicates_dropped_same_content"] == 2
    assert res.manifest["is_synthetic"] is True
    lines = res.data_path.read_text().splitlines()
    assert len(lines) == 28
    first = json.loads(lines[0])
    assert list(first) == sorted(FIELD_NAMES)  # sort_keys output, exact schema
    assert first["retrieved_at"] == "2026-10-14T09:00:00Z" and "<" not in first["description"]

    rc = validate_jobs.main([str(res.data_path), "--report", str(tmp_path / "report.json")])
    assert rc == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["errors"] == [] and report["stats"]["synthetic_records"] == 28
    # Every one of the nine careers shows up via title mapping in the fixture.
    assert len(report["stats"]["title_career_counts"]) == 9
    # Nothing found, nothing to validate: the gate must fail loudly, not pass silently.
    assert validate_jobs.main(["--raw-dir", str(tmp_path / "empty"), "--report", str(tmp_path / "r2.json")]) == 1


def test_validator_rejects_bad_records(tmp_path):
    reg = load_registry()
    good = [json.loads(x) for x in job_api.run("sample_fixture", out_dir=tmp_path, now=NOW)
            .data_path.read_text().splitlines()]

    def errors_for(mutate):
        recs = copy.deepcopy(good[:3])
        mutate(recs)
        return validate_jobs.validate_records(recs, reg)["errors"]

    assert any("missing required field 'title'" in e for e in errors_for(lambda r: r[0].update(title="")))
    assert any("content_hash does not match" in e for e in errors_for(lambda r: r[0].update(title="Edited")))
    assert any("duplicate job_id" in e for e in errors_for(lambda r: r[1].update(job_id=r[0]["job_id"])))
    assert any("unknown fields" in e for e in errors_for(lambda r: r[0].update(salary="1")))
    assert any("date_posted is after retrieved_at" in e
               for e in errors_for(lambda r: r[0].update(date_posted="2027-01-01T00:00:00Z")))
    assert any("still contains HTML" in e for e in errors_for(
        lambda r: r[0].update(description="<div>" + "x" * 40 + "</div>")))
    assert any("not in registry" in e for e in errors_for(lambda r: r[0].update(source_id="nope")))
    assert any("is_synthetic disagrees" in e for e in errors_for(lambda r: r[0].update(is_synthetic=False)))
    assert errors_for(lambda r: None) == []


def test_skill_dictionary_and_career_taxonomy():
    sd = SkillDictionary.load()
    assert len(sd.skills) == 70
    # Phase 1 normalization examples resolve to one canonical skill.
    for term, canon in [("MS Excel", "excel"), ("Microsoft Excel", "excel"), ("Postgres", "postgresql"),
                        ("Microsoft Power BI", "power_bi"), ("Amazon Web Services", "aws"), ("python3", "python")]:
        assert sd.lookup(term).canonical_name == canon, term
    assert sd.lookup("R").canonical_name == "r" and sd.lookup("golang").canonical_name == "go"
    assert sd.lookup("underwater basket weaving") is None

    # Phase 1 doc example sentence.
    text = ("We are seeking a Junior Data Engineer with experience in Python, SQL, PostgreSQL, "
            "AWS, Docker and Airflow.")
    assert [m.canonical_name for m in sd.scan(text)] == ["python", "sql", "postgresql", "aws", "docker", "airflow"]
    # Ambiguous bare tokens are not matched in prose; unambiguous aliases are.
    assert sd.scan("Ready to go and read R. Go for it.") == []
    assert [m.canonical_name for m in sd.scan("Knowledge of R programming and golang")] == ["r", "go"]
    # Longest phrase wins; inner dots survive; sentence-final dots do not break matching.
    assert [m.canonical_name for m in sd.scan("Build with Node.js. Use CI/CD and Power BI.")] == \
        ["javascript", "ci_cd", "power_bi"]

    # normalize() must stay identical to the Phase 1 validator's.
    spec = importlib.util.spec_from_file_location("vt", ROOT / "scripts" / "validate_taxonomies.py")
    vt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vt)
    for s in ["MS Excel", "CI/CD", "node.js", "A/B Testing", "R&D", "  Power-BI  ", "C++ & C#"]:
        assert normalize(s) == vt.normalize(s)

    ct = CareerTaxonomy.load()
    assert len(ct.careers) == 9
    assert ct.match_title("Senior Full-Stack Developer (Nairobi)").career_id == "CA08"
    assert ct.match_title("Power BI Developer").career_id == "CA02"   # longer alias beats 'developer'
    assert ct.match_title("ML Engineer").career_id == "CA05"
    assert ct.match_title("Marketing Manager") is None
    with pytest.raises(ValueError):
        CareerTaxonomy([], [("CA99", "ghost engineer")])


class _Handler(BaseHTTPRequestHandler):
    calls: list = []
    fail_first = True

    def do_GET(self):  # noqa: N802
        _Handler.calls.append((self.path, self.headers.get("Authorization")))
        if _Handler.fail_first:
            _Handler.fail_first = False
            self.send_response(503)
            self.end_headers()
            return
        page = int(self.path.split("page=")[1].split("&")[0])
        rows = {1: [{"id": "a1", "title": "Data Analyst", "description": "<p>Use SQL and Excel daily to report.</p>",
                     "company": {"name": "Sample Ltd"}, "posted_at": "2026-10-01"},
                    {"id": "a2", "title": "", "description": "bad row without a title"}],
                2: [{"id": "a3", "title": "Cloud Engineer", "description": "Run AWS and Linux infrastructure.",
                     "company": {"name": "Sample Ltd"}, "posted_at": "2026-10-02"}]}.get(page, [])
        body = json.dumps({"results": rows}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def test_http_adapter_pagination_auth_retry_and_skips(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        data = json.loads(DEFAULT_REGISTRY.read_text())
        src = data["sources"][1]
        src.update(enabled=True, terms_reviewed=True, terms_url="https://example.org/terms")
        src["config"]["pagination"]["max_pages"] = 5
        src["config"]["min_interval_s"] = 0.5
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps(data))
        env = {"JOB_API_BASE_URL": f"http://127.0.0.1:{server.server_port}", "JOB_API_KEY": "secret-key"}
        sleeps: list[float] = []
        _Handler.calls, _Handler.fail_first = [], True

        res = job_api.run("job_opportunities_api", registry_path=reg_path, out_dir=tmp_path,
                          now=NOW, env=env, sleep=sleeps.append)
        c = res.manifest["counts"]
        assert c["fetched"] == 3 and c["written"] == 2 and c["skipped_invalid"] == 1
        assert res.manifest["skip_reasons"] == {"missing title": 1}
        recs = [json.loads(x) for x in res.data_path.read_text().splitlines()]
        assert {r["job_id"] for r in recs} == {"job_opportunities_api:a1", "job_opportunities_api:a3"}
        assert recs[0]["company"] == "Sample Ltd" and recs[0]["is_synthetic"] is False
        assert all(a == "Bearer secret-key" for _, a in _Handler.calls)     # auth header on every call
        assert 1.0 in sleeps and 0.5 in sleeps                              # retry backoff + page interval
        assert len(_Handler.calls) == 4                                     # 503 retry, page 1, 2, 3 (empty)

        # Plain-http to a non-local host is refused before any request is made.
        with pytest.raises(job_api.FetchError):
            job_api._check_url("http://example.org/jobs")
    finally:
        server.shutdown()
