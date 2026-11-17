import base64
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_ready_and_compat_health():
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["skills"] == 70
    assert client.get("/api/health").json()["geminiReady"] is True


def test_recommend_validation_and_result():
    ok = client.post("/recommend", json={"profile_id": "p", "skills": ["Python", "SQL", "Excel", "Power BI", "Git"]})
    assert ok.status_code == 200 and ok.json()["careers"][0]["career_name"] == "Data Analyst"
    bad = client.post("/recommend", json={"profile_id": "p", "skills": []})
    assert bad.status_code == 422 and "error" in bad.json()
    assert client.post("/recommend", json={"profile_id": "p", "skills": ["x"], "target_career_id": "CA99"}).status_code == 404


def test_api_key_protection(monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    body = {"profile_id": "p", "skills": ["Python"]}
    assert client.post("/recommend", json=body).status_code == 401
    assert client.post("/recommend", json=body, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/recommend", json=body, headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 401 and client.get("/metrics", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 200  # web UI endpoints stay public


def test_feedback(tmp_path, monkeypatch):
    from src.product import feedback_store
    monkeypatch.setattr("src.config.FEEDBACK_CSV", tmp_path / "f.csv")
    monkeypatch.setattr(feedback_store.config, "FEEDBACK_CSV", tmp_path / "f.csv")
    r = client.post("/feedback", json={"profile_id": "p", "career_id": "CA01", "feedback_type": "useful"})
    assert r.status_code == 200 and (tmp_path / "f.csv").exists()
    assert client.post("/feedback", json={"profile_id": "p", "career_id": "CA99", "feedback_type": "useful"}).status_code == 404
    assert client.post("/feedback", json={"profile_id": "p", "career_id": "CA01", "feedback_type": "bad"}).status_code == 422


def test_ui_analyze_camel_and_snake_json_and_multipart():
    camel = client.post("/api/v1/analyze", json={"currentRole": "x", "skills": ["Python", "SQL"], "targetRole": "Data Engineer", "experienceLevel": "Mid"})
    assert camel.status_code == 200 and camel.json()["targetRoleTitle"] == "Data Engineer"
    snake = client.post("/api/v1/analyze", json={"current_role": "x", "current_skills": ["Python", "SQL"], "target_role": "Data Engineer"})
    assert snake.status_code == 200 and snake.json()["readinessScore"] == camel.json()["readinessScore"]
    mp = client.post("/api/v1/analyze", data={"current_role": "x", "experience_level": "Mid", "current_skills": json.dumps(["Python", "SQL"]), "target_role": "Data Engineer"},
                     files={"resume_file": ("cv.txt", b"I know Docker well", "text/plain")})
    assert mp.status_code == 200 and "Docker" in mp.json()["matchedSkills"]


def test_ui_endpoints_errors_use_error_key():
    r = client.post("/api/v1/analyze", json={"skills": [], "targetRole": "Data Analyst"})
    assert r.status_code == 422 and r.json()["error"]
    assert client.post("/api/v1/analyze", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/api/v1/extract-job", json={}).status_code == 400
    gone = client.post("/api/v1/extract-job", json={"text": "This job posting has expired and is no longer accepting applications."})
    assert gone.status_code == 410 and gone.json()["postingStatus"] == "expired"
    assert client.post("/api/v1/extract-job", json={"url": "http://127.0.0.1:1/x"}).status_code == 400
    assert client.post("/api/v1/chat", json={"message": ""}).status_code == 400


def test_extract_job_and_parse_resume_endpoints():
    r = client.post("/api/v1/extract-job", json={"text": "DevOps Engineer\nRequirements\n- Linux, Docker, Kubernetes, CI/CD"})
    assert r.status_code == 200 and "Kubernetes" in r.json()["requiredSkills"]
    doc = {"name": "cv.txt", "mimeType": "text/plain", "base64Data": base64.b64encode(b"Cloud Engineer\nAWS, Terraform, Linux").decode()}
    p = client.post("/api/v1/parse-resume", json=doc)
    assert p.status_code == 200 and "AWS" in p.json()["detectedSkills"]
    d = client.post("/api/v1/extract-job", json={"document": {"name": "jd.txt", "textContent": "Data Analyst\nRequirements\n- SQL and Excel"}})
    assert d.status_code == 200 and d.json()["requiredSkills"] == ["SQL", "Excel"]


def test_open_source_proxy_unconfigured_is_clean_error():
    r = client.post("/api/coach/open-source", json={"message": "hi", "provider": "groq", "modelId": "meta-llama/Llama-3.3-70B-Instruct"})
    assert r.status_code == 400 and "not configured" in r.json()["error"]
    assert "presets" in client.get("/api/coach/models").json()


def test_reference_endpoints_and_security_headers():
    assert len(client.get("/careers").json()) == 9 and len(client.get("/skills").json()) == 70
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff" and "default-src" in h["content-security-policy"]


def test_rate_limit_and_body_limit(monkeypatch):
    import api.main as m
    monkeypatch.setattr(m, "_limiter", m.RateLimiter(2))
    codes = [client.post("/api/v1/chat", json={"message": "hi"}).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]
    monkeypatch.setattr(m, "_limiter", m.RateLimiter(0))
    big = client.post("/api/v1/chat", content=b"x", headers={"content-length": str(30 * 1024 * 1024), "content-type": "application/json"})
    assert big.status_code == 413


def test_admin_reload_requires_configured_key(monkeypatch):
    assert client.post("/admin/reload").status_code == 403  # no key configured -> disabled
    monkeypatch.setenv("API_KEY", "k")
    assert client.post("/admin/reload").status_code == 401
    r = client.post("/admin/reload", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.json()["reloaded"] is True


@pytest.mark.skipif(not (__import__("api.main", fromlist=["DIST_DIR"]).DIST_DIR / "index.html").exists(), reason="UI not built")
def test_static_ui_serving_and_no_path_traversal():
    assert "<title>Ujuzify" in client.get("/").text
    assert client.get("/vendor/d3.min.js").status_code == 200
    for p in ("/..%2f..%2fetc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/assets/..%2f..%2f..%2fetc%2fpasswd", "/....//....//etc/passwd"):
        r = client.get(p)
        assert "root:" not in r.text, p
    assert client.get("/api/unknown").status_code == 404
    assert "<title>Ujuzify" in client.get("/deep/link/in/spa").text
