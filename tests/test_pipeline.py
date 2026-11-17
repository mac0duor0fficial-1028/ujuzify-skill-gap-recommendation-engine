import csv
import json
from pathlib import Path

import httpx

from src.etl import calculate_skill_demand, label_careers, transform_jobs
from src.ingestion import job_api
from src.nlp import evaluate_skill_extraction, extract_job_skills
from src.preprocessing import validate_jobs

FIX = Path(__file__).parent / "fixtures" / "joa_response.json"


def _client():
    payload = json.loads(FIX.read_text())

    def handler(request: httpx.Request):
        assert request.url.params["country"] == "KE"
        return httpx.Response(200, json=payload if request.url.params["page"] == "1" else {"data": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _cfg(tmp):
    cfg = tmp / "cfg.yaml"
    cfg.write_text("primary_source:\n  source_id: joa\n  base_url: https://example.test\n  endpoint: /public/jobs\n  country: KE\n"
                   "  page_param: page\n  limit_param: limit\n  page_size: 100\n  max_pages: 3\n  results_keys: [data]\n")
    return cfg


def test_full_pipeline_on_fixture(tmp_path):
    raw, norm, n = job_api.run(_cfg(tmp_path), tmp_path / "raw", client=_client())
    assert n == 7 and raw.exists()
    rep = validate_jobs.validate(norm)
    assert rep["ok"] and rep["duplicate_source_ids"] == 1 and rep["non_ke_country"] == 1

    staging = tmp_path / "staging.csv"
    stats = transform_jobs.transform(norm, staging)
    assert stats["dropped_non_kenya"] == 1 and stats["dropped_duplicate_id"] == 1 and stats["dropped_duplicate_hash"] == 1
    assert stats["output"] == 4
    titles = [r["title"] for r in csv.DictReader(open(staging))]
    assert "Junior Data Analyst" in titles

    skills = tmp_path / "skills.csv"
    assert extract_job_skills.run(staging, skills) > 8
    demand = tmp_path / "demand.csv"
    assert calculate_skill_demand.calculate(staging, skills, demand) == 4
    rates = {r["skill_id"]: float(r["demand_rate"]) for r in csv.DictReader(open(demand))}
    assert rates["docker"] == 0.5 and rates["sql"] == 0.5 and rates["airflow"] == 0.25

    lab = tmp_path / "labels.csv"
    labelled, total = label_careers.label(staging, lab)
    assert (labelled, total) == (4, 4)

    m = evaluate_skill_extraction.evaluate(skills, Path(__file__).parent.parent / "data/evaluation/skill_extraction_gold.csv", tmp_path / "m.csv")
    assert m["recall"] == 1.0 and 0 < m["precision"] <= 1.0


def test_pipeline_fails_loudly_without_upstream(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        extract_job_skills.run(tmp_path / "nope.csv", tmp_path / "o.csv")
    with pytest.raises(FileNotFoundError):
        validate_jobs.validate(tmp_path / "nope.csv")


def test_engine_uses_demand_when_present(tmp_path):
    from src.recommendation.recommendation_engine import Engine, load_demand
    from src.taxonomy import get_taxonomy
    d = tmp_path / "d.csv"
    d.write_text("skill_id,job_count,total_jobs,demand_rate\nsql,5,10,0.5\npython,4,10,0.4\n")
    dem = load_demand(d)
    r = Engine(get_taxonomy(), dem).rank_careers(["sql", "python"])
    assert all(m.market_demand is not None for m in r)
    assert load_demand(tmp_path / "missing.csv") == {}
