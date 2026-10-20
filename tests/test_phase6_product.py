"""Phase 6 gate tests: learning resources, FastAPI service, Streamlit app end to end.

Three test functions. The API and UI tests run against a real uvicorn server on a local
port; the UI is driven with Streamlit's AppTest, so requests go through actual HTTP.
"""
from __future__ import annotations

import csv
import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from api.main import create_app
from src.etl import build_labels, build_staging
from src.ingestion import job_api
from src.nlp import extract_job_skills
from src.nlp.skill_dictionary import SkillDictionary
from src.product.client import ApiClient, ApiError, build_payload, md_escape, percent
from src.recommendation import build_career_skills
from src.recommendation.engine import DATA_LIMITS, DISCLAIMER, MODEL_VERSION
from src.recommendation.learning import COLUMNS, ResourceCatalog, attach_resources

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "streamlit_app.py"
T0 = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)
SD = SkillDictionary.load()
ANALYST = ["Excel", "SQL", "Statistics", "Python", "Data Visualization"]
FORBIDDEN = re.compile(r"probab|chance|likelihood|odds", re.I)


@pytest.fixture(scope="module")
def career_skills_csv(tmp_path_factory) -> Path:
    """Run the pipeline on the synthetic fixture into a temp dir; return the career_skills.csv path."""
    d = tmp_path_factory.mktemp("phase6")
    job_api.run("sample_fixture", out_dir=d / "raw", now=T0)
    f = {n: d / f"{n}.csv" for n in ("staging", "labels", "job_skills", "career_skills")}
    assert build_staging.main(["--raw-dir", str(d / "raw"), "--out", str(f["staging"])]) == 0
    assert build_labels.main(["--staging", str(f["staging"]), "--out", str(f["labels"])]) == 0
    assert extract_job_skills.main(["--staging", str(f["staging"]), "--out", str(f["job_skills"])]) == 0
    assert build_career_skills.main(["--staging", str(f["staging"]), "--labels", str(f["labels"]),
                                     "--job-skills", str(f["job_skills"]), "--out", str(f["career_skills"])]) == 0
    return f["career_skills"]


@pytest.fixture(scope="module")
def live_api(career_skills_csv):
    """A real uvicorn server on a free local port, serving the temp career profiles."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(career_skills_path=career_skills_csv), host="127.0.0.1",
                                           port=port, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_learning_resources_catalog_and_matching(tmp_path):
    cat = ResourceCatalog.load()
    assert len(cat.resources) >= 50 and len({r.resource_id for r in cat.resources}) == len(cat.resources)
    assert all(r.url.startswith("https://") and r.skill_id in SD.skills for r in cat.resources)
    for name in ("python", "sql", "excel", "power_bi", "docker", "git"):          # skills the demo careers lean on
        assert cat.options(SD.by_canonical[name].skill_id), name
    assert cat.options("SK64") == []                                               # communication: honestly uncovered

    # Validation rejects bad reference data.
    head = ",".join(COLUMNS)
    ok = "LR001,python,Python Tutorial,PSF,https://docs.python.org/3/tutorial/,beginner,documentation,free,1"
    def load(*rows, header=head):
        p = tmp_path / "res.csv"
        p.write_text("\n".join([header, *rows]) + "\n")
        return ResourceCatalog.load(p)
    assert len(load(ok).resources) == 1
    for row, msg in [(ok.replace("python,", "not_a_skill,", 1), "unknown skill"),
                     (ok.replace("https://", "http://"), "https URL"),
                     (ok.replace(",free,", ",gratis,"), "cost"),
                     (ok.replace(",beginner,", ",expert,"), "difficulty"),
                     (ok.replace(",documentation,", ",podcast,"), "format"),
                     (ok.replace(",free,1", ",free,0"), "priority")]:
        with pytest.raises(ValueError, match=msg):
            load(row)
    with pytest.raises(ValueError, match="duplicate resource_id"):
        load(ok, ok.replace("https://docs.python.org/3/tutorial/", "https://example.org/x"))
    with pytest.raises(ValueError, match="duplicate \\(skill, url\\)"):
        load(ok, ok.replace("LR001", "LR002"))
    with pytest.raises(ValueError, match="columns"):
        load(ok, header="resource_id,skill")

    # Selection order: priority, then difficulty, then id. free_only drops freemium/paid.
    py = SD.by_canonical["python"].skill_id
    mixed = load(
        "LR003,python,C,P,https://example.org/c,beginner,course,free,2",
        "LR001,python,A,P,https://example.org/a,intermediate,course,paid,1",
        "LR002,python,B,P,https://example.org/b,beginner,course,freemium,2",
        "LR004,python,D,P,https://example.org/d,intermediate,course,free,2")
    assert [r.resource_id for r in mixed.options(py)] == ["LR001", "LR002", "LR003", "LR004"]
    assert [r.resource_id for r in mixed.options(py, free_only=True)] == ["LR003", "LR004"]

    # attach_resources: primary + at most two alternatives; clear notes when nothing fits.
    items = [{"skill_id": py}, {"skill_id": "SK64"}]
    attach_resources(items, mixed)
    assert items[0]["resource"]["resource_id"] == "LR001" and len(items[0]["alternatives"]) == 2
    assert items[0]["resource_note"] is None and items[1]["resource"] is None and "yet" in items[1]["resource_note"]
    paid_only = load("LR001,python,A,P,https://example.org/a,beginner,course,paid,1")
    one = [{"skill_id": py}]
    attach_resources(one, paid_only, free_only=True)
    assert one[0]["resource"] is None and "No free resource" in one[0]["resource_note"]
    assert mixed.coverage({py: 3.0, "SK64": 1.0}) == pytest.approx(0.75) and mixed.coverage({}) == 0.0


def test_api_endpoints_validation_and_failure_modes(career_skills_csv, tmp_path):
    with TestClient(create_app(career_skills_path=career_skills_csv)) as c:
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["model_version"] == MODEL_VERSION and h["careers"] == 9
        assert h["synthetic_postings"] == h["postings"] == 28 and "synthetic test data" in h["data_notice"]
        assert h["disclaimer"] == DISCLAIMER and h["data_limits"] == DATA_LIMITS
        skills = c.get("/skills").json()
        assert len(skills) == 70 and [s["display_name"].lower() for s in skills] == sorted(s["display_name"].lower() for s in skills)
        careers = c.get("/careers").json()
        assert [x["career_id"] for x in careers] == [f"CA{i:02d}" for i in range(1, 10)] and all(x["low_sample"] for x in careers)

        # Happy path: ranked, explained, resourced, wording rules.
        r = c.post("/recommend", json={"skills": ANALYST, "top": 2})
        body = r.json()
        assert r.status_code == 200 and body["top_n"] == 2 and len(body["recommendations"]) == 9
        assert body["recommendations"][0]["career_id"] == "CA01" and body["model_version"] == MODEL_VERSION
        scores = [m["score"] for m in body["recommendations"]]
        assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)
        assert body["disclaimer"] == DISCLAIMER and body["data_limits"] == DATA_LIMITS
        assert body["gap"]["missing"] and all(g["resource"] or g["resource_note"] for g in body["gap"]["missing"])
        assert not FORBIDDEN.search(json.dumps(body))
        again = c.post("/recommend", json={"skills": ANALYST, "top": 2}).json()
        assert {**again, "created_at": 0} == {**body, "created_at": 0}                  # deterministic

        # Options: target career, free-only, other model, unrecognised skills echoed back.
        t = c.post("/recommend", json={"skills": ["SQL"], "target_career": "CA03"}).json()
        assert t["gap"]["career_id"] == "CA03" and t["target_source"] == "requested"
        free = c.post("/recommend", json={"skills": ["SQL"], "target_career": "CA07", "free_only": True}).json()
        assert all(g["resource"] is None or g["resource"]["cost"] == "free" for g in free["gap"]["missing"])
        assert all(g["resource"] or g["resource_note"] for g in free["gap"]["missing"])   # a reason when nothing is free
        pop = c.post("/recommend", json={"skills": ["SQL"], "model": "popularity_v0"}).json()
        assert pop["model_version"] == "popularity_v0"
        part = c.post("/recommend", json={"skills": ["SQL", "Underwater Basket Weaving"]}).json()
        assert part["unrecognized_skills"] == ["Underwater Basket Weaving"] and part["recognized_skills"] == ["SQL"]

        # Validation: every one of these must be a clean 422, never a 500.
        bad = [{"skills": []}, {"skills": ["SQL"] * 51}, {"skills": ["x" * 81]}, {"skills": ["  "]},
               {"skills": "SQL"}, {"skills": ["SQL"], "bogus": 1}, {"skills": ["SQL"], "top": 0},
               {"skills": ["SQL"], "top": 10}, {"skills": ["SQL"], "target_career": "data analyst"},
               {"skills": ["SQL"], "profile_id": "a b/c"}, {"skills": ["SQL"], "model": "magic_v9"},
               {"skills": ["SQL"], "target_career": "CA42"}, {"skills": ["Underwater Basket Weaving"]}, {}]
        for payload in bad:
            resp = c.post("/recommend", json=payload)
            assert resp.status_code == 422, payload
        assert c.get("/recommend").status_code == 405
        assert c.post("/recommend", content="not json", headers={"content-type": "application/json"}).status_code == 422

    # Data not built: clear 503 with the fix, but the taxonomy endpoint still works.
    with TestClient(create_app(career_skills_path=tmp_path / "missing.csv")) as c:
        for path in ("/health", "/careers"):
            resp = c.get(path)
            assert resp.status_code == 503 and "build_career_skills.py" in resp.json()["detail"]
        assert c.post("/recommend", json={"skills": ["SQL"]}).status_code == 503
        assert c.get("/skills").status_code == 200


def test_streamlit_app_end_to_end(live_api, monkeypatch):
    monkeypatch.setenv("UJUZIFY_API_URL", live_api)
    texts = lambda at, kind: [e.value for e in getattr(at, kind)]  # noqa: E731

    # 1. First load: disclaimer and synthetic-data warning shown, no results, no crash.
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert DISCLAIMER in texts(at, "info") and any("synthetic test data" in w for w in texts(at, "warning"))
    assert not at.subheader or "Career matches" not in texts(at, "subheader")
    assert at.multiselect(key="skills_select").options and len(at.multiselect(key="skills_select").options) == 70

    # 2. Submitting nothing gives a message, not results or a crash.
    at.button[0].click().run()
    assert not at.exception and any("at least one skill" in w for w in texts(at, "warning"))
    assert "Career matches" not in texts(at, "subheader")

    # 3. Analyst-style skills plus a typed alias and an unknown skill.
    at.multiselect(key="skills_select").set_value(["Excel", "SQL", "Statistics", "Python", "Data Visualization"])
    at.text_input(key="extra_skills").set_value("MS Excel, Underwater Basket Weaving, [click](http://x.example)")
    at.button[0].click().run()
    assert not at.exception
    md = texts(at, "markdown")
    assert md[0].startswith("**1. Data Analyst**") and "compatibility" in md[0]
    assert "What to learn next for Data Analyst" in texts(at, "subheader")
    assert any(m.startswith("Learn: [") for m in md)                      # a resource link is shown
    unknown_warning = next(w for w in texts(at, "warning") if "did not recognise" in w)
    assert "Underwater Basket Weaving" in unknown_warning
    assert "[click](http" not in unknown_warning and "\\[click\\]\\(http" in unknown_warning   # typed text is escaped, not a live link
    captions = texts(at, "caption")
    assert DISCLAIMER in captions and DATA_LIMITS in captions and not FORBIDDEN.search(" ".join(md + captions))
    assert len(at.get("progress")) >= 1

    # 4. Pick a target career and free-only: the gap section follows the choice.
    at.selectbox(key="target_select").select("Data Engineer")
    at.checkbox(key="free_only").check()
    at.button[0].click().run()
    assert not at.exception
    assert "What to learn next for Data Engineer" in texts(at, "subheader") and "Chosen by you." in texts(at, "caption")
    assert not any("freemium" in m or "paid" in m for m in texts(at, "markdown") if m.startswith(("Learn:", "Also:")))

    # 5. API unreachable: a readable error and no results, not a stack trace.
    monkeypatch.setenv("UJUZIFY_API_URL", "http://127.0.0.1:9")
    down = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not down.exception and any("Cannot reach the Ujuzify API" in e for e in texts(down, "error"))
    assert not down.get("form")

    # Client and helper behaviour, against the same live server.
    client = ApiClient(live_api)
    assert client.health()["status"] == "ok" and len(client.skills()) == 70 and len(client.careers()) == 9
    with pytest.raises(ApiError) as exc:
        client.recommend({"skills": [], "top": 5})
    assert exc.value.status == 422 and "skills" in exc.value.message
    with pytest.raises(ApiError, match="Cannot reach"):
        ApiClient("http://127.0.0.1:9", timeout=1).health()
    p = build_payload(["Excel", "SQL"], "excel, Python; python\nGit", "CA01", True)
    assert p["skills"] == ["Excel", "SQL", "Python", "Git"] and p["target_career"] == "CA01" and p["free_only"] is True
    assert "target_career" not in build_payload(["SQL"], "", None, False)
    for args in ((["SQL"] * 0, "", None, False), ([], " , ; ", None, False), ([f"s{i}" for i in range(51)], "", None, False),
                 (["x" * 81], "", None, False)):
        with pytest.raises(ValueError):
            build_payload(*args)
    assert percent(0.5612) == "56%" and md_escape("a_b*c[d](e)") == "a\\_b\\*c\\[d\\]\\(e\\)"
