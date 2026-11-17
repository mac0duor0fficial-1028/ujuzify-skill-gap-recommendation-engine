import json
import os
from pathlib import Path

import httpx
import pytest

from src.recommendation import evaluate_recommendations, run_recommendation
from src.service import job_parser, llm, workspace
from src.service.errors import EngineError

ROOT = Path(__file__).resolve().parent.parent
_REAL_CLIENT = httpx.Client


def _patch_httpx(monkeypatch, handler):
    monkeypatch.setattr(job_parser, "_assert_public_url", lambda u: None)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))


def test_fetch_job_url_json_ld(monkeypatch):
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Data Analyst",
          "description": "<p>Requirements</p><ul><li>SQL</li><li>Excel and Power BI</li></ul>",
          "hiringOrganization": {"name": "Acme"}, "jobLocation": {"address": {"addressLocality": "Nairobi", "addressCountry": "KE"}}}
    html = f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'
    _patch_httpx(monkeypatch, lambda r: httpx.Response(200, text=html))
    res = workspace.extract_job("https://jobs.example/1", None, None)
    assert res["jobTitle"] == "Data Analyst" and res["company"] == "Acme" and res["location"] == "Nairobi, KE"
    assert {"SQL", "Excel", "Power BI"} <= set(res["requiredSkills"]) and res["sourceMethod"] == "Direct Server Fetch"


def test_fetch_job_url_plain_html_and_failures(monkeypatch):
    body = "<html><body><h1>DevOps Engineer</h1><h2>Requirements</h2><ul><li>Linux</li><li>Docker, Kubernetes</li></ul>" + "<p>We build reliable systems for customers.</p>" * 6 + "</body></html>"
    _patch_httpx(monkeypatch, lambda r: httpx.Response(200, text=body))
    assert "Docker" in workspace.extract_job("https://x.example/j", None, None)["requiredSkills"]
    _patch_httpx(monkeypatch, lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(EngineError) as e:
        workspace.extract_job("https://x.example/j", None, None)
    assert e.value.code == "JOB_UNAVAILABLE"
    _patch_httpx(monkeypatch, lambda r: httpx.Response(403, text="blocked"))
    with pytest.raises(EngineError) as e:
        workspace.extract_job("https://x.example/j", None, None)
    assert e.value.code == "FETCH_BLOCKED"
    _patch_httpx(monkeypatch, lambda r: httpx.Response(200, text="<html>expired: this job posting has expired. No longer accepting applications. " + "x" * 200 + "</html>"))
    with pytest.raises(EngineError) as e:
        workspace.extract_job("https://x.example/j", None, None)
    assert e.value.status == 410


def test_fetch_redirect_to_private_is_blocked(monkeypatch):
    monkeypatch.setattr(job_parser, "socket", job_parser.socket)
    calls = []

    def fake_assert(url):
        calls.append(url)
        if "internal" in url:
            raise EngineError("That address is not allowed.", 400)

    monkeypatch.setattr(job_parser, "_assert_public_url", fake_assert)
    monkeypatch.setattr(httpx, "Client", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "http://internal.example/x"})), **{k: v for k, v in kw.items() if k != "transport"}))
    with pytest.raises(EngineError):
        job_parser.fetch_job_url("https://public.example/a")
    assert len(calls) == 2


def test_llm_resolution_and_chat(monkeypatch):
    for k in ("LLM_BASE_URL", "GROQ_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY", "HF_TOKEN", "OLLAMA_BASE_URL", "OPENSOURCE_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(EngineError):
        llm.resolve("auto", "")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    t = llm.resolve("auto", "meta-llama/Llama-3.3-70B-Instruct")
    assert t["provider"] == "groq" and t["model"] == "llama-3.3-70b-versatile"
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": " hello "}}]})

    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)).post(url, headers=kw["headers"], json=kw["json"]))
    assert llm.chat("hi", [{"role": "user", "text": "earlier"}], {"a": 1}, t) == "hello"
    assert seen["auth"] == "Bearer k" and seen["body"]["messages"][-1]["content"] == "hi"
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(500, request=httpx.Request("POST", url)))
    with pytest.raises(EngineError):
        llm.chat("hi", [], {}, t)


def test_chat_endpoint_falls_back_when_llm_fails(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    r = TestClient(app).post("/api/v1/chat", json={"message": "hello"})
    assert r.status_code == 200 and r.json()["engine"] == "ujuzify-rule-coach"


def test_batch_recommend_and_evaluate(tmp_path):
    prof = ROOT / "data" / "graduate_profiles" / "example_profile.csv"
    rec, gap = run_recommendation.run(prof, tmp_path)
    assert rec.exists() and gap.exists()
    rows = rec.read_text().splitlines()
    assert len(rows) == 1 + 3 * 9
    m = evaluate_recommendations.evaluate(prof, ROOT / "data" / "evaluation" / "career_recommendation_gold.csv", tmp_path / "m.csv")
    assert m["hit_at_1"] == 1.0 and m["mrr"] == 1.0
    with pytest.raises(FileNotFoundError):
        run_recommendation.run(tmp_path / "missing.csv", tmp_path)


def test_analysis_uses_market_demand_when_available(monkeypatch):
    from src.recommendation.recommendation_engine import get_engine
    eng = get_engine()
    monkeypatch.setattr(eng, "demand", {"sql": 0.6, "excel": 0.5, "power_bi": 0.4, "python": 0.5})
    r = workspace.analyze({"skills": ["Python", "SQL", "Excel", "Power BI"], "targetRole": "Data Analyst"})
    assert r["engine"]["demandDataUsed"] is True and r["engine"]["marketAdjustment"] is not None


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to run PostgreSQL integration test")
def test_postgres_schema_and_loader():
    import psycopg
    from src.etl.load_postgres import load
    counts = load(os.environ["TEST_DATABASE_URL"])
    assert counts["skills"] == 70 and counts["careers"] == 9
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as c:
        assert c.execute("select count(*) from curated.career_skills").fetchone()[0] > 90
