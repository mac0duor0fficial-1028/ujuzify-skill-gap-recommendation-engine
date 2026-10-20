# Phase 6 — Learning Resources, API and Web App

Scope: give every skill gap something actionable to do, expose the engine through a small HTTP API, and put a Streamlit front end on it. Authentication, rate limiting, TLS, persistence, feedback capture, Docker and CI are Phase 7.

## Run order (Phase 6 gate)
```bash
pip install -r requirements.txt -r requirements-phase6.txt
python src/recommendation/build_career_skills.py        # Phase 5 output the API reads
python src/recommendation/recommend.py --expect-top CA01
uvicorn api.main:app --host 127.0.0.1 --port 8000       # API   (docs at /docs)
streamlit run app/streamlit_app.py                       # app   (UJUZIFY_API_URL defaults to http://127.0.0.1:8000)
pytest                                                   # 15 tests (5 + 2 + 2 + 3 + 3)
bash scripts/smoke_phase6.sh                             # starts both servers, checks them, stops them
```

## Learning resources (`data/reference/learning_resources.csv`, `src/recommendation/learning.py`)
60 hand-authored entries covering 54 of the 70 skills (58 free, 2 freemium; 41 beginner, 19 intermediate). Each gap item gets a primary resource and up to two alternatives. Order: authored `priority`, then easier difficulty, then id. `free_only` keeps only resources whose cost is `free`; if none is left the item says so instead of showing a paid one. Where no resource exists the item says "No learning resource is listed for this skill yet."

Share of each career's skill importance that has at least one resource (on the synthetic profiles): Cloud 89%, Data Analyst 86%, Software 86%, DevOps 84%, Data Engineer 82%, Data Scientist 81%, ML Engineer 74%, BI Analyst 56%, Cybersecurity 39%; 76% overall. The uncovered skills are mostly soft skills (problem solving, teamwork, communication) plus ETL, data modeling, data warehousing, SIEM and cybersecurity fundamentals. Closing those gaps is a content task, not a code task.

**Link verification, plainly:** the build sandbox could not reach these sites. I confirmed two entries by web search (dbt Learn, Tableau free training). The rest are official documentation or well-known course roots written from memory and **not machine-checked**; some may have moved. Run a link check and have someone review the list before showing it to users. The loader validates structure only (https URL, known skill, allowed cost/difficulty/format, no duplicates).

## API (`api/main.py`)
| Endpoint | Purpose |
|---|---|
| `GET /health` | readiness, model version, a data notice (says when postings are synthetic); 503 with the fix if `career_skills.csv` is not built |
| `GET /skills` | the 70-skill taxonomy for pickers |
| `GET /careers` | careers with posting counts and `low_sample` |
| `POST /recommend` | `{skills, target_career?, top?, model?, free_only?, profile_id?}` → ranked careers with evidence, the gap list with resources, disclaimer, data limits, `model_version`, `created_at` |

Validation rejects (422) empty or oversized skill lists, skills over 80 characters, unknown fields, bad `top`, malformed career or profile ids, unknown models, unknown target careers, and requests where no skill is recognised. Skills it does not recognise are returned in `unrecognized_skills`, not dropped silently. The API is stateless: nothing is stored or logged about a profile.

## Web app (`app/streamlit_app.py`, `src/product/client.py`)
A single page: pick skills (or type aliases such as "MS Excel"), optionally choose a target career and free-only resources, press *Get recommendations*. It shows ranked careers with compatibility, sample size and tie/low-sample warnings, an expandable "why" per career, then the prioritised gap list with reasons and learning links, and the disclaimer and data-limits text. A banner states when the data behind the profiles is synthetic. If the API is down it shows how to start it.

Security choices: the API address comes only from the `UJUZIFY_API_URL` environment variable (not a user field); text a user types is escaped before display (tested with a Markdown link); the app stores nothing.

## What the tests cover
Three new tests (15 in total here). The API and UI tests run against a **real uvicorn server on a local port**, and the UI is driven with Streamlit's `AppTest`, so the app script, its HTTP calls and the API run for real. They cover first load, an empty submit, a full analyst flow with an alias and an unknown skill, target career and free-only, and the API being down. `scripts/smoke_phase6.sh` additionally starts the real `uvicorn` and `streamlit run` processes and checks `/health`, `POST /recommend`, Streamlit's health endpoint and that its page is served.

## Limits and known issues
- **No browser was used.** `AppTest` runs the page script, but real rendering (layout, widgets in a browser) was not checked. Open the app once by hand before demoing.
- **Not production-ready:** no authentication, rate limiting or TLS; keep both servers on localhost until Phase 7. Profiles and recommendations are not stored (Phase 7 adds the tables and the API that writes them).
- The learning list is a starter set (see above); resources have no duration estimates, and proficiency, time and budget beyond free-only are not used.
- All numbers come from 30 synthetic postings, so every career is `low_sample` and the compatibility percentages are illustrations.
- Scores are compatibility scores, not probabilities of being hired; the disclaimer and data-limits text appear in every response and on the page.
- With newer Starlette versions the tests print a deprecation warning about `httpx` in `TestClient`; it is harmless.
- `scripts/smoke_phase6.sh` needs bash and curl (use WSL or Git Bash on Windows).
