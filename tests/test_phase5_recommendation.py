"""Phase 5 gate tests: career profiles, scoring models, gap analysis, evaluation, CLI.

Three test functions. The README gates are cumulative; Phases 2-4 use nine and this phase
adds three.
"""
from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.etl import build_labels, build_staging
from src.etl.common import CAREER_SKILL_COLUMNS, RECOMMENDATION_COLUMNS, EtlError
from src.evaluation import recommendation_eval as ev
from src.ingestion import job_api
from src.nlp import extract_job_skills
from src.nlp.skill_dictionary import SkillDictionary
from src.recommendation import build_career_skills, recommend as rec
from src.recommendation.engine import (
    DATA_LIMITS, DISCLAIMER, MODEL_VERSION, MODELS, gap_analysis, load_prerequisites, rank_careers,
    recommend, resolve_skills, score_keyword_overlap, score_popularity, score_tfidf_cosine,
    score_weighted_coverage,
)
from src.recommendation.profiles import CorpusCounts, Posting

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)
SD = SkillDictionary.load()
ID = lambda name: SD.by_canonical[name].skill_id  # noqa: E731


def _toy_postings() -> list[Posting]:
    sql, xl, py, pd_ = ID("sql"), ID("excel"), ID("python"), ID("pandas")
    return [
        Posting("a1", "CA01", {sql: 1.0, xl: 1.0}),
        Posting("a2", "CA01", {sql: 1.0, xl: 0.5}),
        Posting("a3", "CA01", {sql: 1.0}),
        Posting("a4", "CA01", {sql: 1.0, xl: 1.0}),
        Posting("b1", "CA03", {sql: 1.0, py: 1.0}),
        Posting("b2", "CA03", {py: 1.0, pd_: 1.0}),
    ]


def test_profiles_scoring_models_and_gap_analysis(tmp_path):
    names = {"CA01": "Data Analyst", "CA03": "Data Engineer"}
    postings = _toy_postings()
    counts = CorpusCounts(postings, names, min_postings=3)
    profiles = counts.profiles()
    sql, xl, py, pd_ = ID("sql"), ID("excel"), ID("python"), ID("pandas")

    # Profile arithmetic checked by hand: excel is in 3 of 4 DA postings, one of them "a plus".
    da = profiles["CA01"]
    assert (da.postings, da.low_sample) == (4, False) and profiles["CA03"].low_sample is True
    assert (da.skills[sql].frequency, da.skills[sql].importance) == (1.0, 1.0)
    assert da.skills[xl].frequency == 0.75 and da.skills[xl].importance == pytest.approx(2.5 / 4)
    assert all(st.importance <= st.frequency for p in profiles.values() for st in p.skills.values())

    # Leave-one-out equals a profile built from the remaining postings (independent rebuild).
    held = postings[1]
    rebuilt = CorpusCounts([p for p in postings if p is not held], names, 3).profiles()
    loo = counts.profiles(exclude=held)
    assert {c: {s: (st.count, round(st.frequency, 9), round(st.importance, 9)) for s, st in p.skills.items()}
            for c, p in loo.items()} == \
           {c: {s: (st.count, round(st.frequency, 9), round(st.importance, 9)) for s, st in p.skills.items()}
            for c, p in rebuilt.items()}
    assert counts.profiles(exclude=postings[4])["CA03"].postings == 1  # held-out career keeps its other posting

    # Scoring models by hand. DA importance mass: sql 1 + excel 0.625 = 1.625; DE: sql .5 + python 1 + pandas .5 = 2.0
    have = frozenset({sql, xl})
    wc = score_weighted_coverage(have, profiles)
    assert wc["CA01"] == pytest.approx(1.0) and wc["CA03"] == pytest.approx(0.5 / 2.0)
    assert score_weighted_coverage(frozenset({sql}), profiles)["CA01"] == pytest.approx(1 / 1.625)
    assert score_weighted_coverage(frozenset(), profiles) == {"CA01": 0.0, "CA03": 0.0}
    assert score_popularity(have, profiles) == {"CA01": pytest.approx(4 / 6), "CA03": pytest.approx(2 / 6)}
    assert score_popularity(frozenset(), profiles) == score_popularity(frozenset({py}), profiles)  # profile-blind
    ko = score_keyword_overlap(have, profiles)
    assert ko == {"CA01": 1.0, "CA03": 0.5}
    tf = score_tfidf_cosine(have, profiles)
    assert all(0.0 <= v <= 1.0 + 1e-9 for v in tf.values()) and tf["CA01"] > tf["CA03"]
    # TF-IDF cosine worked out by hand: 2 careers; sql is in both (idf 1), excel/python/pandas in one
    # (idf ln(3/2)+1). Query = {sql, excel}; career vectors use posting frequency x idf.
    i1, i2 = 1.0, math.log(3 / 2) + 1
    q_norm = math.sqrt(i1 ** 2 + i2 ** 2)
    v1 = {"sql": 1.0 * i1, "excel": 0.75 * i2}
    v3 = {"sql": 0.5 * i1, "python": 1.0 * i2, "pandas": 0.5 * i2}
    assert tf["CA01"] == pytest.approx((1 * v1["sql"] + i2 * v1["excel"]) / (q_norm * math.sqrt(sum(x * x for x in v1.values()))))
    assert tf["CA03"] == pytest.approx((1 * v3["sql"]) / (q_norm * math.sqrt(sum(x * x for x in v3.values()))))
    assert score_tfidf_cosine(frozenset({ID("kafka")}), profiles) == {"CA01": 0.0, "CA03": 0.0}

    # Ranking, ties and evidence. Python-only profile: DA has no python, DE does.
    r = recommend({py}, profiles, SD)
    assert [m.career_id for m in r] == ["CA03", "CA01"] and r[0].score > r[1].score == 0.0
    tied = recommend(frozenset(), profiles, SD)                      # all scores 0 -> arbitrary order, flagged
    assert [m.tied_with for m in tied] == [1, 1] and tied[0].career_id == "CA01"  # volume breaks the tie
    top = recommend(have, profiles, SD)[0]
    assert [e.canonical_name for e in top.matched] == ["sql", "excel"]            # sorted by importance
    assert top.matched[0].reason.startswith("you have it; appears in 100% of 4 sampled Data Analyst postings")
    assert top.missing == [] and top.model_version == MODEL_VERSION
    de_missing = recommend(have, profiles, SD)[1].missing
    assert [e.canonical_name for e in de_missing] == ["python", "pandas"] and all(e.skill_id in SD.skills for e in de_missing)
    with pytest.raises(ValueError, match="unknown model"):
        recommend(have, profiles, SD, model="magic_v9")

    # Gap analysis: prerequisites come first even when the dependent skill is more important.
    prereq = load_prerequisites(None, SD)
    assert prereq[ID("pandas")] == [ID("python")]
    de = profiles["CA03"]
    gap = gap_analysis({ID("sql")}, de, SD, prereq)
    assert [g.canonical_name for g in gap.missing] == ["python", "pandas"]         # python (1.0) before pandas (.5)
    boosted = CorpusCounts([Posting("x", "CA03", {py: 0.5, pd_: 1.0})], names, 1).profiles()["CA03"]
    gap2 = gap_analysis(frozenset(), boosted, SD, prereq)
    assert [g.canonical_name for g in gap2.missing] == ["python", "pandas"]         # pandas more important, python first
    assert "missing, learn first" in gap2.missing[1].reason and gap2.missing[1].requires == [("Python", False)]
    gap3 = gap_analysis({py}, boosted, SD, prereq)
    assert [g.canonical_name for g in gap3.missing] == ["pandas"] and "Python (you have it)" in gap3.missing[0].reason
    assert gap3.missing[0].resource is None                                         # resources arrive in Phase 6

    # Reference-data validation and profile input handling.
    bad = tmp_path / "pre.csv"
    bad.write_text("skill,requires,rationale\npandas,python,x\npython,pandas,x\n")
    with pytest.raises(ValueError, match="cycle"):
        load_prerequisites(bad, SD)
    bad.write_text("skill,requires,rationale\npandas,not_a_skill,x\n")
    with pytest.raises(ValueError, match="unknown skill"):
        load_prerequisites(bad, SD)
    ids, unknown = resolve_skills(["MS Excel", "Postgres", "Underwater Basket Weaving", "excel"], SD)
    assert ids == {ID("excel"), ID("postgresql")} and unknown == ["Underwater Basket Weaving"]


def test_recommendation_metrics_and_evaluation_harness(tmp_path):
    # Metric arithmetic: ranked A,B,C with B and C acceptable.
    ranked, rel = ["A", "B", "C"], {"B", "C"}
    assert ev.precision_at_k(ranked, rel, 3) == pytest.approx(2 / 3)
    assert ev.recall_at_k(ranked, rel, 3) == 1.0 and ev.recall_at_k(ranked, rel, 1) == 0.0
    ideal = 1 / math.log2(2) + 1 / math.log2(3)
    assert ev.ndcg_at_k(ranked, rel, 3) == pytest.approx((1 / math.log2(3) + 1 / math.log2(4)) / ideal)
    assert ev.ndcg_at_k(["B", "C", "A"], rel, 3) == pytest.approx(1.0)
    assert ev.reciprocal_rank(ranked, rel) == 0.5 and ev.reciprocal_rank(ranked, {"Z"}) == 0.0
    assert (ev.hit_at_k(ranked, rel, 1), ev.hit_at_k(ranked, rel, 3)) == (0.0, 1.0)
    s = ev.summarize({"p1": ["A", "B"], "p2": ["B", "A"]}, {"p1": {"B"}, "p2": {"B"}, "p3": set()})
    assert s["profiles"] == 2 and s["hit@1"] == 0.5 and s["mrr"] == 0.75   # p3 has no labels: excluded

    # Gold profile file validation.
    cids = {"CA01", "CA03"}
    good = tmp_path / "gold.csv"
    good.write_text("profile_id,skills,acceptable_careers,note\nP1,sql|excel,CA01,x\n")
    assert ev.load_gold_profiles(good, SD, cids)[0]["acceptable"] == {"CA01"}
    for body, msg in [("P1,not_a_skill,CA01,x", "unknown skill"), ("P1,sql,CA99,x", "acceptable_careers"),
                      ("P1,sql,CA01,x\nP1,sql,CA01,y", "duplicate")]:
        good.write_text("profile_id,skills,acceptable_careers,note\n" + body + "\n")
        with pytest.raises(ValueError, match=msg):
            ev.load_gold_profiles(good, SD, cids)
    for real in (ROOT / "data" / "evaluation" / "profile_gold_dev.csv",):
        careers = {r["career_id"] for r in csv.DictReader((ROOT / "data/external/career_taxonomy.csv").open())}
        assert len(ev.load_gold_profiles(real, SD, careers)) >= 10

    # Leave-one-out: a career whose only posting is held out cannot be evaluated and is skipped.
    names = {"CA01": "Data Analyst", "CA03": "Data Engineer"}
    posts = _toy_postings() + [Posting("c1", "CA05", {ID("pytorch"): 1.0})]
    out = ev.evaluate_leave_one_out(posts, {**names, "CA05": "ML Engineer"}, keep_fraction=1.0, min_postings=1)
    assert out["skipped_postings"] == 1 and set(out["models"]) == set(MODELS)
    assert out["models"][MODEL_VERSION]["profiles"] == 6
    # keep_fraction is deterministic and monotone in what it keeps.
    assert [ev._keep("a1", "SK01", 1.0), ev._keep("a1", "SK01", 0.0)] == [True, False]
    assert ev._keep("a1", "SK09", 0.6) == ev._keep("a1", "SK09", 0.6)


def test_pipeline_cli_demo_gate_and_explainability_rules(tmp_path):
    raw = tmp_path / "raw"
    job_api.run("sample_fixture", out_dir=raw, now=T0)
    f = {n: tmp_path / f"{n}.csv" for n in ("staging", "labels", "job_skills", "career_skills", "recs")}
    assert build_staging.main(["--raw-dir", str(raw), "--out", str(f["staging"])]) == 0
    assert build_labels.main(["--staging", str(f["staging"]), "--out", str(f["labels"])]) == 0
    assert extract_job_skills.main(["--staging", str(f["staging"]), "--out", str(f["job_skills"])]) == 0
    assert build_career_skills.main(["--staging", str(f["staging"]), "--labels", str(f["labels"]),
                                     "--job-skills", str(f["job_skills"]), "--out", str(f["career_skills"])]) == 0
    with f["career_skills"].open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == CAREER_SKILL_COLUMNS and {r["career_id"] for r in rows} == {f"CA{i:02d}" for i in range(1, 10)}
    assert all(float(r["importance"]) <= float(r["frequency"]) <= 1 for r in rows)

    # GATE: the demo profile ranks Data Analyst (CA01) first; a wrong expectation exits 2.
    base = ["--career-skills", str(f["career_skills"]), "--out", str(f["recs"]), "--json", str(tmp_path / "r.json")]
    assert rec.main(base + ["--expect-top", "CA01"]) == 0
    assert rec.main(base + ["--expect-top", "CA03"]) == 2

    # Stored recommendations carry model_version and creation time (rule 5), scores stay in [0, 1].
    with f["recs"].open(newline="") as fh:
        recs = list(csv.DictReader(fh))
    assert list(recs[0]) == RECOMMENDATION_COLUMNS and [r["rank"] for r in recs] == [str(i) for i in range(1, 10)]
    assert recs[0]["career_id"] == "CA01" and {r["model_version"] for r in recs} == {MODEL_VERSION}
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", r["created_at"]) for r in recs)
    assert all(0 <= float(r["score"]) <= 1 for r in recs)
    assert [float(r["score"]) for r in recs] == sorted((float(r["score"]) for r in recs), reverse=True)

    # Explainability rules: evidence, data limits, wording.
    result = json.loads((tmp_path / "r.json").read_text())
    assert result["disclaimer"] == DISCLAIMER and result["data_limits"] == DATA_LIMITS
    first = result["recommendations"][0]
    assert first["matched"] and first["missing"] and first["low_sample"] is True
    assert all(e["reason"] and e["postings"] > 0 for e in first["matched"] + first["missing"])
    assert result["gap"]["career_id"] == "CA01" and result["gap"]["missing"][0]["priority"] == 1
    assert all(g["reason"] and g["resource"] is None for g in result["gap"]["missing"])
    profile = rec.load_profile(rec.DEFAULT_PROFILE)
    text = rec.render(rec.run(profile, rows, now=T0))
    assert "compatibility" in text and DISCLAIMER in text and DATA_LIMITS in text
    assert not re.search(r"probab|chance|likelihood|odds", text, re.I)
    assert "Learning resources" in text and "[low sample: 4 postings]" in text

    # Not hard-wired to Data Analyst: a DevOps-style profile and a security profile rank their own careers first.
    devops = rec.run({"profile_id": "d", "skills": ["Docker", "Kubernetes", "CI/CD", "Linux", "Git", "Terraform"]}, rows)
    assert devops["recommendations"][0]["career_id"] in {"CA06", "CA07"}
    sec = rec.run({"profile_id": "s", "skills": ["SIEM", "incident response", "network security"]}, rows)
    assert sec["recommendations"][0]["career_id"] == "CA09"
    ignored = rec.run({"profile_id": "u", "skills": ["SQL", "Underwater Basket Weaving"]}, rows)
    assert ignored["unrecognized_skills"] == ["Underwater Basket Weaving"]
    assert "Not recognised (ignored): Underwater Basket Weaving" in rec.render(ignored)
    explicit = rec.run({"profile_id": "t", "skills": ["SQL"]}, rows, target="CA03")
    assert explicit["gap"]["career_id"] == "CA03" and explicit["target_source"] == "requested"

    # Failure paths exit non-zero with a message instead of a traceback.
    nothing = tmp_path / "none.json"
    nothing.write_text(json.dumps({"profile_id": "n", "skills": ["Underwater Basket Weaving"]}))
    assert rec.main(base + ["--profile", str(nothing)]) == 1
    assert rec.main(base + ["--target", "CA42"]) == 1
    assert rec.main(["--career-skills", str(tmp_path / "missing.csv"), "--out", str(f["recs"])]) == 1
    with pytest.raises(EtlError, match="not in staging"):
        build_career_skills.build(
            list(csv.DictReader(f["staging"].open())), list(csv.DictReader(f["labels"].open())),
            [{"job_id": "ghost:1", "skill_id": "SK01", "importance": "1.0"}])

    # The evaluation script runs on the same data and reports all four models.
    report = tmp_path / "eval.json"
    assert ev.main(["--career-skills", str(f["career_skills"]), "--staging", str(f["staging"]),
                    "--labels", str(f["labels"]), "--job-skills", str(f["job_skills"]), "--report", str(report)]) == 0
    rep = json.loads(report.read_text())
    assert set(rep["gold_profiles"]) == set(MODELS) and len(rep["leave_one_out"]) == 2
    assert rep["gold_profiles"][MODEL_VERSION]["hit@1"] > rep["gold_profiles"]["popularity_v0"]["hit@1"]

    # database/schema.sql mirrors the career_skills CSV contract.
    ddl = (ROOT / "database" / "schema.sql").read_text()
    body = re.search(r"CREATE TABLE IF NOT EXISTS staging\.career_skills \((.*?)\n\);", ddl, re.S).group(1)
    assert [ln.split()[0] for ln in body.splitlines() if ln.strip()] == CAREER_SKILL_COLUMNS
