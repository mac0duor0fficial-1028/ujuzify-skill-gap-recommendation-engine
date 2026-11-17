# Ujuzify: Graduate Skill-Gap Recommendation Engine

Explainable career and skill-gap recommendations for Kenyan technology graduates. One container serves the
**web interface** (`app/`) and the **FastAPI engine** (`api/`, `src/`).

```
Browser UI (app/dist)  ──►  FastAPI (api/main.py)  ──►  taxonomy-governed engine (src/)
                                   │                         ├─ NLP skill extraction   (src/nlp)
                                   │                         ├─ career ranking + gaps  (src/recommendation)
                                   │                         ├─ resources + projects   (src/product)
                                   │                         └─ UI adapters + coach    (src/service)
Batch pipeline (src/pipeline.py, airflow/): jobs API ► ETL ► NLP ► skill demand ► PostgreSQL
```

## Quick start

```bash
# 1. Docker (UI + API on http://localhost:8000)
cp .env.example .env            # set API_KEY and POSTGRES_PASSWORD
docker compose up --build

# 2. Local development
pip install -r requirements-dev.txt
cd app && npm ci && npm run build && cd ..
uvicorn api.main:app --reload --port 8000        # UI at http://localhost:8000, docs at /docs
# UI hot-reload: `npm run dev` in app/ (proxies /api to :8000)
python -m pytest -q                              # 60 tests
python scripts/check_environment.py
```

## What it does

| Capability | Where |
|---|---|
| Parse a CV (PDF/DOCX/TXT/paste) into canonical skills | `POST /api/v1/parse-resume` |
| Extract requirements from a job URL, file or pasted text (SSRF-protected fetch, JSON-LD aware) | `POST /api/v1/extract-job` |
| Skill-gap analysis: readiness score, matched / partial / missing, competency radar, prioritised roadmap with resources and portfolio projects | `POST /api/v1/analyze` |
| Context-aware career coach (7-day sprints, CV bullet templates, mock interviews, "why this score"); optional LLM passthrough | `POST /api/v1/chat` |
| Machine API for ranked careers + gaps + resources + projects (API key) | `POST /recommend`, `POST /feedback` |
| Reference data | `GET /careers`, `GET /skills` |
| Ops | `/health`, `/ready`, `/metrics` (key), `POST /admin/reload` (key) |

The scoring model is documented in [docs/engine.md](docs/engine.md). Scores are **compatibility indicators,
never probabilities of employment**.

## Real data: run the pipeline

The engine works out of the box on the expert-seeded career profiles. To add **observed market demand** from
Kenyan postings:

```bash
docker compose --profile pipeline up -d db
docker compose --profile pipeline run --rm pipeline          # ingest → validate → ETL → NLP → demand → PostgreSQL
curl -X POST localhost:8000/admin/reload -H "X-API-Key: $API_KEY"   # pick up skill_demand.csv without restart
```

Or locally: `python -m src.pipeline --load-db`. Daily scheduling: `airflow/dags/ujuzify_pipeline.py`.
Acquisition is API-first (`data/external/acquisition_config.yaml`); **verify the endpoint parameters against the
source's current documentation and terms before running** (see "Known limits").

## Configuration

See `.env.example`. Key variables: `API_KEY`, `RATE_LIMIT_PER_MINUTE`, `CORS_ORIGINS`, `APP_ENV`,
`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` (optional coach LLM), `DATABASE_URL` (pipeline loading).

## Repository layout

```
app/          web UI (Vite SPA; builds to app/dist, served by FastAPI)
api/          FastAPI app, security (API key, rate limit), schemas
src/          taxonomy, ingestion, preprocessing, etl, nlp, recommendation, product, service, pipeline
data/         external (taxonomies, schemas, config) · reference (career profiles, resources, projects) ·
              evaluation (dev gold files) · raw/processed/feedback (generated, git-ignored)
database/     schema.sql (raw → staging → curated → analytics, app)
airflow/      daily DAG
monitoring/   Prometheus metrics, health probe
scripts/      environment check, MLflow baseline logging
tests/        60 tests (unit, API, pipeline on a fixture, optional PostgreSQL integration)
docs/         engine, deployment
```

## Known limits (read before going live)

- **No labelled outcome data exists**, so no supervised model is trained. Hit@k/MRR in `data/evaluation/` are
  *development fixtures* that validate the evaluator, not accuracy claims.
- Career profile weights are expert seeds until the pipeline has produced real demand data; demand rates are
  sample statistics of whatever the source returned.
- Extraction is rule-based over a 70-skill taxonomy: unseen synonyms are missed, and "required vs preferred" is
  inferred from section headings. Non-technology roles work only when the posting's requirements can be extracted.
- Learning-resource URLs are curated and must be re-verified periodically (`data/reference/learning_resources.csv`).
- Image CVs are rejected (no OCR). Legacy `.doc` is rejected.
- Rate limiting is per process and per IP; use a gateway for multi-replica deployments.
- The Docker image and Compose stack were written to standard patterns but could **not be built in the
  authoring environment** (no Docker daemon); CI builds and smoke-tests the image on every push.
