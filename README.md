# Ujuzify — Graduate Skill-Gap Recommendation Engine

An end-to-end machine learning system that helps Kenyan university/TVET graduates and early-career professionals find suitable technology career paths, see which skills they are missing, and get a prioritized learning roadmap based on job-market evidence.

**Stack:** Python • NLP • PostgreSQL • Airflow • FastAPI • Streamlit • MLflow • Docker • AWS (planned) • Power BI (planned)

> Compatibility scores are **not** probabilities of employment. See `docs/explainability_requirements.md`.

## Status
| Phase | Scope | Status |
|---|---|---|
| 0 | Repository scaffold | Done |
| 1 | Problem definition, taxonomies, data model, evaluation, explainability | Done |
| 2 | Data acquisition: source registry, raw schema, ingestion, validation | Done (offline fixture; live API source needs configuration) |
| 3 | ETL and PostgreSQL | Next |
| 4 | NLP skill extraction | Planned |
| 5 | Career classification and skill-gap engine | Planned |
| 6 | Learning resources, FastAPI, Streamlit | Planned |
| 7 | Docker, CI/CD, monitoring, MLflow | Planned |
| 8-12 | Advanced models, Power BI, AWS | Planned |

Test gates (cumulative passing tests): 10 after Phase 5, 13 after Phase 6, 15 after Phase 7. Phase 0-1 add no pytest tests.

## Quick start
Target Python version: **3.13** (matches the Phase 7 Docker image).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then replace placeholder secrets
python scripts/validate_taxonomies.py
python src/ingestion/job_api.py        # Phase 2: offline sample source
python src/ingestion/validate_jobs.py
pytest
```

Expected from the taxonomy check: `TAXONOMY VALIDATION PASSED` with 9 careers and 70 skills.

## Repository layout
```
graduate-skill-gap-engine/
├── data/
│   ├── raw/ interim/ processed/    # generated, git-ignored contents
│   ├── external/                   # governed taxonomies and source config (tracked)
│   ├── reference/                  # career profiles, learning resources (tracked)
│   ├── evaluation/                 # gold sets and metrics
│   ├── graduate_profiles/          # example profile inputs
│   └── feedback/                   # user feedback (git-ignored contents)
├── notebooks/                      # 01..07 exploration to evaluation
├── src/
│   ├── ingestion/ preprocessing/ etl/ nlp/ features/
│   └── models/ recommendation/ product/ evaluation/
├── airflow/dags/   database/   api/   app/   models/   powerbi/
├── monitoring/   scripts/   tests/   docs/   .github/workflows/
├── .env.example   .gitignore   LICENSE   requirements.txt
```
`docker-compose.yml` and phase-specific requirements files are added in the phases that use them.

## Documentation
- `docs/phase1_problem_definition.md` — problem, users, scope, architecture, strategy
- `docs/phase2_data_acquisition.md` — source registry, raw schema, ingestion and validation
- `docs/data_model.md` — entities, fields, relationships
- `docs/evaluation_strategy.md` — metrics and baselines
- `docs/explainability_requirements.md` — explanation and scoring rules

## Principles
Explainability • Actionability • Budget awareness • Portfolio orientation • Catalog transparency • Feedback readiness.
Data is acquired through APIs and permitted feeds only; no scraping that bypasses terms or access controls. Check each source's terms before redistributing data.

## License
MIT. See `LICENSE` (replace `[Your Name]` with your name).
