# Phase 1 — Problem Definition, Scope & Architecture

## 1. Overview
Ujuzify is an end-to-end machine learning system that helps Kenyan university/TVET graduates and early-career professionals identify suitable technology career paths, understand which skills they are missing, and receive a personalized learning roadmap grounded in job-market evidence. It is built as a portfolio-grade data product combining data engineering, NLP, recommendation, ML, APIs, dashboards and cloud deployment.

## 2. Problem statement
Many Kenyan graduates have a mix of academic, technical and soft skills but no data-driven way to determine:
- which technology careers fit their current profile,
- how their skills compare with actual job requirements,
- which skills are missing, and
- which missing skills to learn first.

The system answers these by combining job-market data, skill extraction, profile matching, skill-gap analysis and personalized recommendations.

## 3. Target users
| Type | Users |
|---|---|
| Primary | Kenyan university/TVET graduates and early-career professionals seeking entry-level or junior technology careers |
| Secondary | Career coaches and mentors; university and TVET career offices; students researching technology careers; recruiters and organizations interested in aggregate skill-market insights |

## 4. MVP scope
In scope:
1. Graduate profile creation
2. Current-skill input and normalization
3. Career-path recommendations
4. Target-career skill-gap analysis
5. Priority ranking of missing skills
6. Personalized learning-path recommendations
7. Explanation of why recommendations were generated

Out of scope:
- Automated job applications
- Guaranteed employment predictions
- Salary guarantees
- Psychometric or personality diagnosis
- Fully autonomous career decisions
- Data collection that violates website terms or applicable law
- Financial recommendations

## 5. Career taxonomy (nine MVP careers)
Defined in `data/external/career_taxonomy.csv`.

| ID | Career | Family |
|---|---|---|
| CA01 | Data Analyst | Data |
| CA02 | Business Intelligence Analyst | Data |
| CA03 | Data Engineer | Data |
| CA04 | Data Scientist | Data |
| CA05 | Machine Learning Engineer | Data |
| CA06 | Cloud Engineer | Cloud / Infrastructure |
| CA07 | DevOps Engineer | Cloud / Infrastructure |
| CA08 | Software Engineer | Software |
| CA09 | Cybersecurity Analyst | Security |

The taxonomy is deliberately small. More careers can be added once the job-market dataset is large and diverse enough to support them.

## 6. Skill taxonomy foundation
Defined in `data/external/skill_taxonomy.csv`: 70 canonical skills across eight categories (programming, analytics_bi, data_science_ml, data_engineering, cloud_infrastructure, software_engineering, security, professional). Each skill has a stable `skill_id` (SK01–SK70), a snake_case `canonical_name`, a `display_name`, a `category` and pipe-separated `aliases`.

Normalization examples (canonical across the project):

| Input variants | Canonical |
|---|---|
| MS Excel / Microsoft Excel / Excel | `excel` |
| Postgres / PostgreSQL | `postgresql` |
| Power BI / Microsoft Power BI | `power_bi` |
| Amazon Web Services / AWS | `aws` |

Governance notes:
- The skill list is an authored starting vocabulary for the MVP, not a claim about the Kenyan labour market. Phase 2–4 evidence (observed job postings) will validate and extend it.
- Ambiguous short tokens (for example `r`, `go`) are listed only with unambiguous aliases (`r programming`, `golang`). Phase 4 must handle their context-sensitive matching.
- `python3`, `node.js` and similar variants resolve to a single skill; `sql` is kept separate from named databases such as `postgresql`.
- Run `python scripts/validate_taxonomies.py` after any edit; it enforces 9 careers, 70 skills, unique IDs and no alias collisions.

## 7. System inputs and outputs
### Graduate profile inputs
Education level; field of study; current technical and professional skills; skill proficiency; years of experience; projects; certifications; preferred or target career; preferred work arrangement; preferred location; learning budget; weekly learning availability.

The MVP starts with the minimum fields needed for meaningful recommendations (current skills). The remaining fields are preserved for later personalization.

### Career recommendation output (example)
- Data Analyst — 91%
- Business Intelligence Analyst — 86%
- Data Engineer — 64%

These are compatibility scores, **not** probabilities of employment.

### Skill-gap output (example, target: Data Engineer)
- Already have: Python, SQL
- Missing: Linux, Docker, Airflow, Cloud, Spark
- Priority: Linux → Docker → Airflow → Cloud → Spark

### Learning recommendation output
Considers skill importance in the target career, prerequisite relationships, current proficiency, target role, resource relevance, learning difficulty, and user constraints such as time and budget.

## 8. High-level architecture
Kenyan job sources → Data ingestion → ETL / validation → PostgreSQL → NLP / feature engineering → Recommendation engine → FastAPI → Streamlit; Power BI for market analytics; AWS for production deployment.

| Layer | Responsibility |
|---|---|
| Job sources | Job postings and related market data |
| Ingestion | Collection and initial validation |
| ETL / validation | Cleaning, normalization, transformation |
| PostgreSQL | Central structured store |
| NLP / features | Skill extraction, normalization, feature creation |
| Recommendation engine | Career matching, skill-gap analysis, learning recommendations |
| FastAPI | ML inference API |
| Streamlit | Graduate-facing application |
| Power BI | Job-market and skill-demand analytics |
| AWS | Production deployment |

## 9. ML strategy
The first engine is interpretable and content-based, forming a baseline for later methods.

Model progression:
1. Frequency-based career recommendation baseline
2. Keyword/content-based skill matching
3. TF-IDF with cosine similarity
4. Embedding-based semantic similarity
5. Hybrid recommendation (skills, semantic similarity, user constraints)
6. Learning-to-rank once enough interaction/evaluation data exists

Initial concept: for each career, build a skill profile from observed job postings, then compare a graduate's normalized skills against it using skill importance. Career Score = weighted similarity between the user's skills and the career's required skills.

## 10. NLP strategy
Convert unstructured job descriptions into normalized, machine-readable skills.

Example: "We are seeking a Junior Data Engineer with experience in Python, SQL, PostgreSQL, AWS, Docker and Airflow." → `python, sql, postgresql, aws, docker, airflow`.

## 11. Related documents
- `docs/data_model.md`
- `docs/evaluation_strategy.md`
- `docs/explainability_requirements.md`

## 12. Phase 1 completion checklist
- [x] Problem and target users defined
- [x] MVP scope defined
- [x] Initial career taxonomy defined
- [x] Inputs and outputs defined
- [x] System architecture designed
- [x] Initial recommendation strategy selected
- [x] NLP approach defined
- [x] Database entities defined
- [x] Evaluation metrics defined
- [x] Repository structure created
- [x] Explainability requirements defined
