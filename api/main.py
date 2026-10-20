"""Ujuzify API (Phase 6).

Run from the project root:

    uvicorn api.main:app --host 127.0.0.1 --port 8000

Endpoints
    GET  /health      readiness, model version and a data notice (503 if data is not built)
    GET  /skills      the skill taxonomy, for building skill pickers
    GET  /careers     the careers that have profiles, with sample sizes
    POST /recommend   career matches + skill gap + learning resources for a skill list

The API is stateless: it stores nothing and does not log skill lists. It has no
authentication, rate limiting or TLS yet (Phase 7), so keep it on localhost. Every
recommendation response carries the required disclaimer and data-limits text, and scores
are compatibility scores, not probabilities of being hired (docs/explainability_requirements.md).
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etl.common import CAREER_SKILL_COLUMNS, CAREER_SKILLS_CSV, EtlError, read_csv  # noqa: E402
from src.nlp.skill_dictionary import SkillDictionary  # noqa: E402
from src.recommendation import recommend as rec  # noqa: E402
from src.recommendation.engine import (  # noqa: E402
    DATA_LIMITS, DISCLAIMER, MODEL_VERSION, MODELS, PREREQUISITES_CSV, load_prerequisites,
)
from src.recommendation.learning import RESOURCES_CSV, ResourceCatalog  # noqa: E402

API_VERSION = "0.6.0"


class RecommendRequest(BaseModel):
    """Input for /recommend. Unknown fields are rejected."""
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field("anonymous", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    skills: list[str] = Field(min_length=1, max_length=50, description="skill names or aliases, e.g. 'MS Excel'")
    target_career: str | None = Field(None, pattern=r"^CA\d{2}$", description="career_id for the gap analysis")
    top: int = Field(5, ge=1, le=9)
    model: str = Field(MODEL_VERSION, max_length=40)
    free_only: bool = False

    @field_validator("skills")
    @classmethod
    def _clean_skills(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v]
        if any(not s or len(s) > 80 for s in cleaned):
            raise ValueError("each skill must be 1-80 characters")
        return cleaned


class _State:
    """Everything loaded at startup. ``error`` is set instead if the data is not built."""
    dictionary: SkillDictionary
    rows: list[dict] | None = None
    catalog: ResourceCatalog | None = None
    prerequisites: dict | None = None
    error: str | None = None


def _load(state: _State, career_skills: Path, resources: Path, prerequisites: Path) -> None:
    state.dictionary = SkillDictionary.load()
    try:
        state.rows = read_csv(career_skills, CAREER_SKILL_COLUMNS)
        if not state.rows:
            raise EtlError("career_skills.csv has no rows")
        state.catalog = ResourceCatalog.load(resources, state.dictionary)
        state.prerequisites = load_prerequisites(prerequisites, state.dictionary)
        state.error = None
    except (EtlError, ValueError, OSError) as e:
        state.rows = None
        state.error = f"{e}"


def create_app(career_skills_path: Path | str | None = None, resources_path: Path | str | None = None,
               prerequisites_path: Path | str | None = None) -> FastAPI:
    paths = (
        Path(career_skills_path or os.environ.get("UJUZIFY_CAREER_SKILLS_CSV") or CAREER_SKILLS_CSV),
        Path(resources_path or RESOURCES_CSV),
        Path(prerequisites_path or PREREQUISITES_CSV),
    )
    state = _State()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _load(state, *paths)
        yield

    app = FastAPI(title="Ujuzify API", version=API_VERSION, lifespan=lifespan,
                  description=DISCLAIMER + " " + DATA_LIMITS)

    def need_data() -> None:
        if state.error:
            raise HTTPException(status_code=503, detail=state.error +
                                " (build it with: python src/recommendation/build_career_skills.py)")

    def corpus_summary() -> dict:
        careers: dict[str, dict] = {}
        for r in state.rows or []:
            careers[r["career_id"]] = {"postings": int(r["postings_in_career"]),
                                       "synthetic": int(r["synthetic_postings_in_career"]),
                                       "low_sample": r["low_sample"] == "true", "name": r["career_name"]}
        return {"careers": careers, "postings": sum(c["postings"] for c in careers.values()),
                "synthetic": sum(c["synthetic"] for c in careers.values())}

    @app.get("/health")
    def health() -> dict:
        need_data()
        s = corpus_summary()
        if s["synthetic"]:
            notice = (f"Demo data: {s['synthetic']} of the {s['postings']} postings behind these career profiles "
                      "are synthetic test data. Results illustrate how the system works; they say nothing "
                      "about the Kenyan job market.")
        else:
            notice = DATA_LIMITS
        return {"status": "ok", "api_version": API_VERSION, "model_version": MODEL_VERSION,
                "models": sorted(MODELS), "careers": len(s["careers"]), "postings": s["postings"],
                "synthetic_postings": s["synthetic"], "data_notice": notice,
                "disclaimer": DISCLAIMER, "data_limits": DATA_LIMITS}

    @app.get("/skills")
    def skills() -> list[dict]:
        return sorted(({"skill_id": s.skill_id, "canonical_name": s.canonical_name,
                        "display_name": s.display_name, "category": s.category}
                       for s in state.dictionary.skills.values()), key=lambda d: d["display_name"].lower())

    @app.get("/careers")
    def careers() -> list[dict]:
        need_data()
        return [{"career_id": cid, "career_name": c["name"], "postings": c["postings"], "low_sample": c["low_sample"]}
                for cid, c in sorted(corpus_summary()["careers"].items())]

    @app.post("/recommend")
    def recommend(req: RecommendRequest) -> dict:
        need_data()
        if req.model not in MODELS:
            raise HTTPException(status_code=422, detail=f"unknown model {req.model!r}; choose from {sorted(MODELS)}")
        try:
            return rec.run({"profile_id": req.profile_id, "skills": req.skills}, state.rows, model=req.model,
                           target=req.target_career, top=req.top, dictionary=state.dictionary,
                           resources=state.catalog, free_only=req.free_only, prerequisites=state.prerequisites)
        except ValueError as e:      # no recognised skills, or a target career without a profile
            raise HTTPException(status_code=422, detail=str(e)) from e

    return app


app = create_app()
