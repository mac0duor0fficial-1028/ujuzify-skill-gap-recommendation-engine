import base64
import io
import zipfile

import pytest

from src.service import coach, workspace
from src.service.documents import detect_unavailable, document_text
from src.service.errors import EngineError
from src.service.job_parser import _assert_public_url, parse_job_text

JD = """Junior Data Engineer
About us
We build fintech.
Requirements
- Experience with Python, SQL and PostgreSQL
- Docker and Airflow
Nice to have
- Amazon Web Services
Responsibilities
- Build and maintain ETL pipelines for analytics teams
"""


def _docx(text: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document>" + "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in text.splitlines()) + "</w:document>")
    return base64.b64encode(buf.getvalue()).decode()


def _pdf(text: str) -> str:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
    return base64.b64encode(out).decode()


def test_job_parsing_sections():
    r = parse_job_text(JD)
    assert r["jobTitle"] == "Junior Data Engineer" and r["seniority"] == "Entry"
    assert {"Python", "SQL", "PostgreSQL", "Docker", "Apache Airflow"} <= set(r["requiredSkills"])
    assert r["preferredSkills"] == ["AWS"] and r["keyResponsibilities"]
    assert r["matchedCareerId"] == "CA03" and r["competencyPillars"]


def test_job_parsing_rejects_empty_and_unavailable():
    with pytest.raises(EngineError) as e:
        parse_job_text("   ")
    assert e.value.status == 400
    with pytest.raises(EngineError) as e:
        parse_job_text("Sorry, this job posting has expired. No longer accepting applications.")
    assert e.value.code == "JOB_UNAVAILABLE" and e.value.status == 410
    with pytest.raises(EngineError) as e:
        parse_job_text("Barista wanted. Must love coffee and smiling at customers every morning.")
    assert e.value.code == "NO_SKILLS_FOUND"


def test_document_extraction_docx_pdf_txt_and_rejections():
    assert "Python" in document_text({"name": "cv.docx", "base64Data": _docx("Jane Doe\nPython and SQL")})
    assert "Python" in document_text({"name": "cv.pdf", "base64Data": _pdf("Python and SQL developer")})
    assert document_text({"textContent": "plain text"}) == "plain text"
    with pytest.raises(EngineError):
        document_text({"name": "scan.png", "mimeType": "image/png", "base64Data": base64.b64encode(b"x").decode()})
    with pytest.raises(EngineError):
        document_text({"name": "x.pdf", "base64Data": base64.b64encode(b"%PDF-garbage").decode()})
    with pytest.raises(EngineError):
        document_text({"name": "x.docx", "base64Data": base64.b64encode(b"PKnotzip").decode()})
    assert detect_unavailable("Page not found") and not detect_unavailable("Great role. " * 200)


@pytest.mark.parametrize("url", ["http://localhost/x", "http://127.0.0.1/x", "http://169.254.169.254/latest", "ftp://x.com/a", "http://10.0.0.5/a"])
def test_ssrf_guard(url):
    with pytest.raises(EngineError):
        _assert_public_url(url)


def test_resume_parse():
    r = workspace.parse_resume({"textContent": "Jane Doe\nData Analyst\nSkills: SQL, Excel, Power BI. 3 years of experience."})
    assert r["detectedRole"] == "Data Analyst" and {"SQL", "Excel", "Power BI"} <= set(r["detectedSkills"]) and r["detectedSeniority"] == "Mid"


def test_analyze_career_profile_path():
    r = workspace.analyze({"skills": ["Python", "SQL", "Excel", "Power BI", "Git"], "targetRole": "Data Analyst", "experienceLevel": "Mid"})
    assert r["targetRoleTitle"] == "Data Analyst" and r["readinessScore"] >= 60
    assert "Python" in r["matchedSkills"] and r["roadmap"] and r["roadmap"][0]["resources"]
    assert {c["area"] for c in r["competencyScores"]} and all(0 <= c["current"] <= 100 for c in r["competencyScores"])
    assert r["engine"]["demandDataUsed"] is False and "not a probability" in r["engine"]["scoreNote"]


def test_analyze_partial_credit_and_cv_skills():
    r = workspace.analyze({"skills": ["MySQL"], "targetRole": "Data Analyst",
                           "resume": {"textContent": "I use Excel daily and know Tableau."}})
    assert "SQL" in r["partialSkills"] and "Excel" in r["matchedSkills"] and "Tableau" in r["matchedSkills"]


def test_analyze_generic_requirements_for_non_taxonomy_role():
    r = workspace.analyze({"skills": ["Patient Triage"], "targetRole": "Nurse Manager",
                           "extractedTarget": {"jobTitle": "Clinical Nurse Manager", "requiredSkills": ["Patient Triage", "Staff Budgeting"], "preferredSkills": [], "competencyPillars": []}})
    assert r["matchedSkills"] == ["Patient Triage"] and r["missingSkills"] == ["Staff Budgeting"] and r["readinessScore"] == 50


def test_analyze_errors():
    with pytest.raises(EngineError):
        workspace.analyze({"skills": [], "targetRole": "Data Analyst"})
    with pytest.raises(EngineError) as e:
        workspace.analyze({"skills": ["Python"], "targetRole": "Astronaut"})
    assert e.value.code == "NO_REQUIREMENTS"


def test_coach_intents_and_honesty():
    ctx = {"skills": ["Python"], "analysis": {"targetRoleTitle": "Data Engineer", "readinessScore": 40, "matchedSkills": ["Python"],
                                              "partialSkills": [], "missingSkills": ["Docker"], "roadmap": [{"skill": "Docker", "priority": "High", "estimatedTime": "1 week", "projectIdea": "x"}], "executiveSummary": "S."}}
    assert "One-week plan: Docker" in coach.reply("plan for Docker", [], ctx)["reply"]
    assert "[" in coach.reply("write cv bullets", [], ctx)["reply"]  # placeholders, no invented metrics
    assert "probability of getting hired" in coach.reply("why is my score 40", [], ctx)["reply"]
    assert "can't predict salaries" in coach.reply("how much salary will I get", [], ctx)["reply"]
    assert "Kubernetes" in coach.reply("what is kubernetes", [], {})["reply"]
    with pytest.raises(ValueError):
        coach.reply("   ")
