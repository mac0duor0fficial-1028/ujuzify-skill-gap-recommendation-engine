"""Ujuzify FastAPI service: machine API, web-interface backend and static hosting of the built UI."""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.schemas import FeedbackIn, GraduateProfile
from api.security import RateLimiter, require_admin_key, require_api_key
from monitoring import metrics as M
from src import config
from src.product.feedback_store import record_feedback
from src.product.personalization import build_recommendation
from src.recommendation.recommendation_engine import get_engine
from src.service import coach, llm, workspace
from src.service.errors import EngineError
from src.taxonomy import CATEGORY_LABELS, get_taxonomy

MAX_BODY_BYTES = 18 * 1024 * 1024
DIST_DIR = Path(os.getenv("UJUZIFY_UI_DIST", config.ROOT / "app" / "dist"))

app = FastAPI(title="Ujuzify Skill-Gap Recommendation Engine", version=config.APP_VERSION,
              description="Explainable career and skill-gap recommendations for Kenyan technology graduates.")

_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Content-Type", "X-API-Key", "Authorization"], max_age=600)

_limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MINUTE", "90") or 0))
_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; "
        f"connect-src 'self' {os.getenv('CSP_CONNECT_SRC', 'http://localhost:* http://127.0.0.1:*')}; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def _err(status: int, message: str, code: str | None = None, extra: dict | None = None) -> JSONResponse:
    body = {"error": message, "detail": message}
    if code:
        body["errorCode"] = code
    body.update(extra or {})
    return JSONResponse(status_code=status, content=body)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    start = time.perf_counter()
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        return _err(413, "Request body too large")
    if path.startswith(("/api/", "/recommend", "/feedback")):
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()) or (request.client.host if request.client else "?")
        if not _limiter.allow(ip):
            return _err(429, "Too many requests. Please slow down and try again shortly.", "RATE_LIMITED")
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if not path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers.setdefault("Content-Security-Policy", _CSP)
    if os.getenv("APP_ENV", "development") == "production":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    label = path if path in {"/health", "/ready", "/recommend", "/feedback", "/metrics", "/careers", "/skills"} or path.startswith("/api/") else "static"
    M.REQUESTS.labels(request.method, label, response.status_code).inc()
    M.LATENCY.labels(label).observe(time.perf_counter() - start)
    return response


@app.exception_handler(EngineError)
async def _engine_error(_: Request, exc: EngineError):
    M.ERRORS.labels(exc.code or str(exc.status)).inc()
    return _err(exc.status, exc.message, exc.code, exc.extra)


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException):
    return _err(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", [])[1:])
    return _err(422, f"Invalid request{': ' + loc if loc else ''}: {first.get('msg', 'validation failed')}", "VALIDATION_ERROR")


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):  # never leak internals
    M.ERRORS.labels("internal").inc()
    return _err(500, "Unexpected server error. Please try again.", "INTERNAL_ERROR")


# --------------------------------------------------------------------------- health / ops
@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "version": config.APP_VERSION, "model_version": config.MODEL_VERSION}


@app.get("/ready", tags=["ops"])
def ready():
    tax = get_taxonomy()
    eng = get_engine()
    ok = len(tax.skills) >= 70 and len(tax.careers) >= 9
    return JSONResponse(status_code=200 if ok else 503, content={
        "ready": ok, "skills": len(tax.skills), "careers": len(tax.careers),
        "demand_data_loaded": bool(eng.demand), "ui_built": (DIST_DIR / "index.html").exists()})


@app.get("/api/health", tags=["ops"])
def api_health():
    # `geminiReady` is kept for the bundled web interface's status indicator (it means "engine ready").
    return {"ok": True, "engineReady": True, "geminiReady": True, "model": "ujuzify-engine", "version": config.APP_VERSION}


@app.get("/metrics", tags=["ops"], dependencies=[Depends(require_api_key)])
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/admin/reload", tags=["ops"], dependencies=[Depends(require_admin_key)])
def admin_reload():
    """Reload observed skill-demand data after a pipeline run (no restart needed)."""
    eng = get_engine(reload=True)
    return {"reloaded": True, "demand_skills": len(eng.demand)}


@app.get("/careers", tags=["reference"])
def careers():
    tax = get_taxonomy()
    return [{"career_id": c.career_id, "career_name": c.name, "family": c.family, "description": c.description,
             "required_skills": [{"skill": tax.display(s), "importance": w} for s, w in sorted(c.skills.items(), key=lambda x: -x[1])]}
            for c in tax.careers.values()]


@app.get("/skills", tags=["reference"])
def skills():
    tax = get_taxonomy()
    return [{"skill_id": s.canonical_name, "name": s.display_name, "category": CATEGORY_LABELS.get(s.category, s.category)} for s in tax.skills.values()]


# ------------------------------------------------------------------ machine API (API key)
@app.post("/recommend", tags=["recommendation"], dependencies=[Depends(require_api_key)])
def recommend(profile: GraduateProfile):
    t0 = time.perf_counter()
    M.RECOMMENDATIONS.inc()
    try:
        data = profile.model_dump()
        top_n, target = data.pop("top_n"), data.pop("target_career_id")
        result = build_recommendation(data, target, top_n)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    finally:
        M.RECOMMENDATION_LATENCY.observe(time.perf_counter() - t0)
    return result


@app.post("/feedback", tags=["recommendation"], dependencies=[Depends(require_api_key)])
def feedback(body: FeedbackIn):
    if body.career_id not in get_taxonomy().careers:
        raise HTTPException(status_code=404, detail=f"Unknown career id: {body.career_id}")
    M.FEEDBACK.labels(body.feedback_type).inc()
    return {"status": "recorded", "feedback": record_feedback(body.profile_id, body.career_id, body.feedback_type, body.comment)}


# ------------------------------------------------------------- web interface backend (/api/v1)
def _pick(d: dict, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


async def _analyze_payload(request: Request) -> dict:
    """Accept the web UI's camelCase JSON as well as snake_case JSON / multipart (the UI's 'FastAPI' mode)."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        raw_skills = form.get("current_skills") or "[]"
        import json as _json
        try:
            skills = _json.loads(raw_skills) if isinstance(raw_skills, str) else []
        except ValueError:
            skills = [s for s in str(raw_skills).split(",") if s.strip()]
        resume = None
        f = form.get("resume_file")
        if f is not None and hasattr(f, "read"):
            data = await f.read()
            if len(data) > 12 * 1024 * 1024:
                raise EngineError("The uploaded file is too large (limit 12 MB).", 413)
            resume = {"name": f.filename, "mimeType": f.content_type, "base64Data": base64.b64encode(data).decode()}
        return {"currentRole": form.get("current_role", ""), "experienceLevel": form.get("experience_level", "Mid"),
                "skills": skills, "targetRole": form.get("target_role", ""), "resume": resume}
    try:
        d = await request.json()
    except ValueError as exc:
        raise EngineError("Request body must be valid JSON.", 400) from exc
    if not isinstance(d, dict):
        raise EngineError("Request body must be a JSON object.", 400)
    resume = _pick(d, "resume")
    if isinstance(resume, dict) and "filename" in resume:  # snake_case resume block
        resume = {"name": resume.get("filename"), "mimeType": resume.get("mime_type"),
                  "textContent": resume.get("text_content") or "", "base64Data": resume.get("base64_data") or ""}
    return {"currentRole": _pick(d, "currentRole", "current_role", default=""),
            "experienceLevel": _pick(d, "experienceLevel", "experience_level", default="Mid"),
            "skills": _pick(d, "skills", "current_skills", default=[]), "targetRole": _pick(d, "targetRole", "target_role", default=""),
            "resume": resume, "targetJobDocument": _pick(d, "targetJobDocument"), "extractedTarget": _pick(d, "extractedTarget")}


@app.post("/api/v1/analyze", tags=["workspace"])
async def v1_analyze(request: Request):
    payload = await _analyze_payload(request)
    if isinstance(payload["skills"], str):
        payload["skills"] = [s for s in payload["skills"].replace(";", ",").split(",") if s.strip()]
    if not isinstance(payload["skills"], list):
        raise EngineError("'skills' must be a list of skill names.", 422)
    if len(payload["skills"]) > 120:
        raise EngineError("Too many skills (limit 120).", 422)
    if len(str(payload["targetRole"])) > 80_000:
        raise EngineError("Target role text is too long.", 413)
    result = workspace.analyze(payload)
    M.ANALYSES.labels("career_profile" if result["engine"]["careerId"] else "extracted_requirements").inc()
    return result


@app.post("/api/v1/extract-job", tags=["workspace"])
async def v1_extract_job(request: Request):
    d = await request.json()
    return workspace.extract_job(_pick(d, "url"), _pick(d, "text"), _pick(d, "document"))


@app.post("/api/v1/scrape-job", tags=["workspace"])
async def v1_scrape(request: Request):
    d = await request.json()
    url = str(_pick(d, "url", default="")).strip()
    res = workspace.extract_job(url, None, None)
    return {"title": res["jobTitle"], "company": res["company"], "description": res["formattedDescription"]}


@app.post("/api/v1/parse-resume", tags=["workspace"])
async def v1_parse_resume(request: Request):
    return workspace.parse_resume(await request.json())


@app.post("/api/v1/chat", tags=["workspace"])
async def v1_chat(request: Request):
    d = await request.json()
    message = str(_pick(d, "message", default="")).strip()
    if not message:
        raise EngineError("Message is required.", 400)
    history, context = _pick(d, "history", default=[]), _pick(d, "context", default={})
    if os.getenv("LLM_BASE_URL"):
        try:
            target = llm.resolve("custom", os.getenv("LLM_MODEL", ""), "")
            return {"reply": llm.chat(message, history, context, target), "sources": [], "engine": f"llm:{target['model']}"}
        except EngineError:
            pass  # fall back to the deterministic coach
    return coach.reply(message, history, context)


@app.get("/api/coach/models", tags=["workspace"])
def coach_models():
    return {"geminiReady": True, "configuredProviders": llm.configured_providers(),
            "presets": [{"id": p["id"], "label": p["label"], "family": p["family"]} for p in llm.PRESETS]}


@app.post("/api/coach/open-source", tags=["workspace"])
async def coach_open_source(request: Request):
    d = await request.json()
    message = str(_pick(d, "message", default="")).strip()
    if not message:
        raise EngineError("Message is required.", 400)
    target = llm.resolve(str(_pick(d, "provider", default="auto")), str(_pick(d, "modelId", default="")), str(_pick(d, "customBaseUrl", default="")))
    reply = llm.chat(message, _pick(d, "history", default=[]), _pick(d, "context", default={}), target)
    return {"reply": reply, "model": target["model"], "provider": target["provider"], "sources": []}


# ----------------------------------------------------------------------- static UI hosting
if (DIST_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets") if (DIST_DIR / "assets").exists() else None

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "docs", "openapi", "redoc")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (DIST_DIR / full_path).resolve()
        if full_path and candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "ujuzify", "message": "UI not built. Run `npm ci && npm run build` in app/ (see README).",
                "docs": "/docs"}
