"""Phase 4 gate tests: skill extractor, evaluation harness, job_skills pipeline.

Two test functions, kept compact on purpose: README gates are cumulative (10 passing
tests after Phase 5); Phases 2 and 3 already use seven.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.etl import build_labels, build_skill_demand, build_staging
from src.etl.common import JOB_SKILL_COLUMNS, EtlError
from src.evaluation.skill_extraction_eval import (
    GOLD_CHALLENGE_CSV, GOLD_DEV_CSV, evaluate, load_gold, prf,
)
from src.ingestion import job_api
from src.nlp import extract_job_skills
from src.nlp.skill_dictionary import SkillDictionary, _tokens
from src.nlp.skill_extractor import EXTRACTOR_METHOD, SkillExtractor, tokenize

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)

# Failures of rule_extractor_v1 on the challenge set that are understood and accepted for now
# (R in a list of non-taxonomy tools, 'Agile ways of working', negation, no stemming x2).
# If a rule change fixes or adds one, this test fails so the change is made knowingly.
KNOWN_CHALLENGE_ERRORS = {"C001", "C021", "C027", "C034", "C040"}


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_extractor_rules_and_evaluation_harness(tmp_path):
    sd = SkillDictionary.load()
    ex = SkillExtractor(sd)
    names = lambda text: sorted(e.canonical_name for e in ex.extract(text))  # noqa: E731

    # Tokenizer parity with the dictionary's, so candidates are identical to Phase 2's.
    gold_texts = [g["text"] for p in (GOLD_DEV_CSV, GOLD_CHALLENGE_CSV) for g in load_gold(p, sd)]
    assert gold_texts and all([t.norm for t in tokenize(x)] == _tokens(x) for x in gold_texts)

    # Context rules, by behaviour.
    assert names("Our R&D team invests in R&D.") == []
    assert names("Go-to-market strategy. Go ahead and apply.") == []
    assert names("Skills: Java, Go, Kubernetes") == ["go", "java", "kubernetes"]
    assert names("Knowledge of R is an advantage.") == ["r"]
    assert names("You will go above and beyond.") == []                       # lowercase 'go' never counts
    assert names("Work with go-getters and self-starters.") == []             # ...even right after 'with'
    assert names("Handle shipping containers at the port.") == []
    assert names("Run containers on Kubernetes.") == ["docker", "kubernetes"]
    assert names("Candidates who excel at sales.") == []
    assert names("Use Microsoft Excel daily.") == ["excel"]                   # multi-word alias is exempt
    assert names("Experience with Docker, Azure, monitoring and Bash scripting.") == \
        ["azure", "bash", "docker", "monitoring"]                             # weak word accepted in a list
    assert names("Strong networking, relationship building and negotiation.") == []
    e = ex.extract("Ideas will spark growth. Apache Spark is used.")
    assert [(x.canonical_name, x.evidence) for x in e] == [("spark", "Apache Spark")]  # evidence is raw text

    # Requirement level and importance (heuristic, but its behaviour is specified).
    by = {x.canonical_name: x for x in ex.extract(
        "Must have SQL. Power BI is a plus. Python required, Tableau a plus. Excel is mentioned.")}
    assert (by["sql"].requirement_level, by["sql"].importance) == ("required", 1.0)
    assert (by["power_bi"].requirement_level, by["power_bi"].importance) == ("preferred", 0.5)
    assert by["tableau"].requirement_level == "unspecified" and by["tableau"].importance == 1.0
    assert by["excel"].requirement_level == "unspecified"
    twice = ex.extract("Python is a plus. Python is required.")[0]
    assert (twice.mentions, twice.importance, twice.requirement_level) == (2, 1.0, "required")

    # Evaluation harness arithmetic: gold {a,b}, predicted {b,c} -> TP=1 FP=1 FN=1.
    gold = [{"doc_id": "d1", "case_tag": "t", "text": "x", "skills": {"a", "b"}}]
    r = evaluate(gold, lambda _t: ["b", "c"])
    assert (r["tp"], r["fp"], r["fn"], r["precision"], r["recall"], r["f1"]) == (1, 1, 1, 0.5, 0.5, 0.5)
    assert prf(0, 0, 0)["f1"] == 0.0
    bad = tmp_path / "bad.csv"
    bad.write_text("doc_id,case_tag,text,skills\nX1,t,hello,not_a_skill\n")
    with pytest.raises(ValueError, match="unknown skill"):
        load_gold(bad, sd)

    # Gold-set regression guards. dev: fitted, so only a regression guard. challenge: written
    # after the rules were frozen, so the extractor must beat the baseline on precision and F1,
    # and its known failures are pinned.
    dev, challenge = load_gold(GOLD_DEV_CSV, sd), load_gold(GOLD_CHALLENGE_CSV, sd)
    assert evaluate(dev, ex.extract_ids)["errors"] == []
    v0, v1 = evaluate(challenge, sd.skill_ids_in), evaluate(challenge, ex.extract_ids)
    assert v1["precision"] > v0["precision"] and v1["f1"] > v0["f1"] and v1["recall"] >= v0["recall"]
    assert {x["doc_id"] for x in v1["errors"]} == KNOWN_CHALLENGE_ERRORS


def test_job_skills_pipeline_traceability_and_demand(tmp_path):
    raw = tmp_path / "raw"
    job_api.run("sample_fixture", out_dir=raw, now=T0)
    staging, labels = tmp_path / "staging.csv", tmp_path / "labels.csv"
    skills_csv, demand = tmp_path / "job_skills.csv", tmp_path / "demand.csv"
    assert build_staging.main(["--raw-dir", str(raw), "--out", str(staging)]) == 0
    assert build_labels.main(["--staging", str(staging), "--out", str(labels)]) == 0
    assert extract_job_skills.main(["--staging", str(staging), "--out", str(skills_csv)]) == 0

    srows = {r["job_id"]: r for r in _rows(staging)}
    rows = _rows(skills_csv)
    assert list(rows[0]) == JOB_SKILL_COLUMNS and rows
    sd = SkillDictionary.load()
    pairs = [(r["job_id"], r["skill_id"]) for r in rows]
    assert len(pairs) == len(set(pairs))                                   # one row per (posting, skill)
    assert {r["job_id"] for r in rows} == set(srows)                       # every fixture posting has skills
    for r in rows:
        text = f"{srows[r['job_id']]['title']}. {srows[r['job_id']]['description']}"
        assert r["skill_id"] in sd.skills and r["method"] == EXTRACTOR_METHOD
        assert r["importance"] in {"0.5", "1.0"} and r["requirement_level"] in {"required", "preferred", "unspecified"}
        assert r["evidence"] and r["evidence"] in text                     # traceable to the posting's own words
        assert r["skill_id"] in set(sd.skill_ids_in(text))                 # never invents beyond dictionary matches
    # The one fixture posting with an explicit cue ("Knowledge of Power BI is an advantage").
    pbi = next(r for r in rows if r["job_id"] == "sample_fixture:SYN-0030" and r["canonical_name"] == "power_bi")
    assert (pbi["requirement_level"], pbi["importance"]) == ("preferred", "0.5")
    assert sum(r["requirement_level"] == "preferred" for r in rows) == 1

    # Skill demand from extractor output: method switches, and a number matches an independent count.
    assert build_skill_demand.main(["--staging", str(staging), "--labels", str(labels),
                                    "--job-skills", str(skills_csv), "--out", str(demand)]) == 0
    drows = _rows(demand)
    assert {r["method"] for r in drows} == {EXTRACTOR_METHOD}
    sql_id = sd.lookup("sql").skill_id
    expected = len({r["job_id"] for r in rows if r["skill_id"] == sql_id})
    sql_all = next(r for r in drows if r["scope"] == "all" and r["skill_id"] == sql_id)
    assert int(sql_all["postings_with_skill"]) == expected > 0 and int(sql_all["postings_in_scope"]) == 28

    # Stale or foreign job_skills input is refused rather than silently mixed in.
    label_rows, staging_rows = _rows(labels), list(srows.values())
    with pytest.raises(EtlError, match="not in staging"):
        build_skill_demand.build(staging_rows, label_rows, job_skills_rows=[{**rows[0], "job_id": "ghost:1"}])
    with pytest.raises(EtlError, match="expected"):
        build_skill_demand.build(staging_rows, label_rows, job_skills_rows=[{**rows[0], "method": "dictionary_scan_v0"}])
    assert extract_job_skills.main(["--staging", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "o.csv")]) == 1

    # database/schema.sql mirrors the job_skills CSV contract.
    ddl = (ROOT / "database" / "schema.sql").read_text()
    body = re.search(r"CREATE TABLE IF NOT EXISTS staging\.job_skills \((.*?)\n\);", ddl, re.S).group(1)
    assert [ln.split()[0] for ln in body.splitlines() if ln.strip()] == JOB_SKILL_COLUMNS
