# Phase 5 — Career Recommendation and Skill-Gap Engine

Scope: rank the nine careers for a graduate profile, explain each ranking with evidence, and list the skills to learn for a target career in priority order. Learning resources (Phase 6) and the API/UI (Phase 7) are not part of this phase.

## Run order (Phase 5 gate)
```bash
python src/recommendation/build_career_skills.py        # staging + labels + job_skills -> data/processed/career_skills.csv
python src/recommendation/recommend.py --expect-top CA01 # demo profile; exit 2 if the top career is not Data Analyst
python src/evaluation/recommendation_eval.py             # metrics for all four models
pytest                                                   # 12 tests (5 + 2 + 2 + 3)
```
`recommend.py` writes every ranked career to `data/processed/recommendations.csv` (with `model_version` and `created_at`) and the full explanation to `data/processed/recommendations.json`. Use `--profile my.json`, `--target CA03`, `--model tfidf_cosine_v0` to vary the run.

## Career profiles (`build_career_skills.py`)
For each career, from its labeled postings: `frequency` = share of postings mentioning the skill; `importance` = mean posting-level importance (1.0, or 0.5 where the posting calls the skill "a plus"; 0 where absent), so `importance <= frequency`. Careers below `--min-postings` (default 30) are flagged `low_sample`. Profiles are generated data (`career_skills.csv`), not code: the engine contains no career or skill catalog (explainability rule 7).

## Models (`src/recommendation/engine.py`)
| Model | Score |
|---|---|
| `popularity_v0` (baseline 1) | career's share of all postings; ignores the profile |
| `keyword_overlap_v0` (baseline 2) | share of the profile's skills that the career mentions |
| `tfidf_cosine_v0` (baseline 3) | TF-IDF cosine between profile and career skill vector |
| `weighted_coverage_v1` (default engine) | sum of career importance over skills you have ÷ sum over all the career's skills |

`weighted_coverage_v1` reads as "you already cover X% of what sampled postings for this career ask for, weighted by importance". Skills you have that the career does not ask for do not count for or against it. Ties are broken by posting volume, then career id, and ties are shown in the output ("tied with N other career(s): order among them is arbitrary").

## Skill-gap analysis
Missing skills for the target career (explicit `--target`, the profile's `target_career`, or the top match) are ordered by importance, then frequency, with prerequisites moved ahead of the skills that build on them when both are missing. Each item states why: frequency, importance and sample size, plus the prerequisite status. Prerequisites come from `data/reference/skill_prerequisites.csv` (22 pairs, **an authored starter set that needs domain review**, validated for unknown names and cycles). Every gap item has a `resource` field that is empty until Phase 6.

## Explainability rules (`docs/explainability_requirements.md`)
1. Compatibility, not probability: output says "compatibility"; a test checks it never uses probability/chance/likelihood wording.
2. Evidence: matched and missing skills with importance, frequency and sample size for every career.
3. Data limits: the required data-limits sentence and the disclaimer are in every output (exact text pinned by a test).
4. No confidence indicator is produced (so none can be mistaken for calibration). `low_sample` and tie flags are shown instead.
5. Traceability: every stored recommendation has `model_version` and `created_at`.
6. Gap reasons are given; links to resources wait for Phase 6.
7. Career profiles and prerequisites are data files.

## Evaluation (`recommendation_eval.py`)
Metrics: Hit@1, Hit@3, MRR, Precision/Recall/NDCG@3. Two evaluations, **both circular on the synthetic fixture**: the fixture's skills were generated per career and the 13 gold profiles were written by the engine's author, so these numbers validate the evaluation pipeline, not real-world accuracy.

| Evaluation | popularity | keyword overlap | TF-IDF cosine | weighted coverage (engine) |
|---|---|---|---|---|
| Gold profiles (13), Hit@1 / MRR | 0.385 / 0.514 | 1.000 / 1.000 | 1.000 / 1.000 | 0.923 / 0.962 |
| Leave-one-out postings (28), all skills, Hit@1 / MRR | 0.143 / 0.238 | 0.786 / 0.875 | 0.929 / 0.964 | 0.929 / 0.964 |
| Leave-one-out postings (28), 60% of skills, Hit@1 / MRR | 0.143 / 0.238 | 0.643 / 0.762 | 0.857 / 0.929 | 0.821 / 0.896 |

What this does and does not show:
- Every profile-aware model beats the popularity baseline by a wide margin.
- **The engine does not beat the TF-IDF baseline here, and does not beat keyword overlap on the gold profiles.** `docs/evaluation_strategy.md` says a new method must beat earlier baselines to justify its complexity; on this data it has not, and the evidence cannot distinguish them (13 and 28 profiles; one profile moves Hit@1 by 4-8 points).
- The engine's single gold miss (P12) is a three-way exact tie at 32% between ML Engineer, Cloud Engineer and DevOps Engineer; the tie-break picked ML Engineer. That is a limit of tiny, lumpy profiles (3 postings per career), not necessarily of the scoring rule.
- The engine is kept as the default because its score has a direct reading ("share of typical requirements already covered") that TF-IDF cosine lacks, and Phase 1 chose an interpretable first engine. That is a design choice, not a measured advantage. Re-run this evaluation on real postings and independently labelled real graduate profiles before relying on either; switching is one flag (`--model tfidf_cosine_v0`) and evidence reasons work for every model.

## Demo gate
The demo profile (`data/reference/demo_profile.json`: Excel, SQL, Statistics, Data Visualization, Python, Communication) was fixed before the first run. Data Analyst ranks first at 56% compatibility (Data Scientist 38%, Business Intelligence Analyst 31%) under the engine, keyword overlap and TF-IDF; only the profile-blind popularity baseline differs. A DevOps-style profile ranks Cloud/DevOps first and a security profile ranks Cybersecurity first, so the result is not hard-wired. All careers are `low_sample` (3-4 postings each), so the percentages are illustrative.

## Not done
- No learned model or embeddings: there are no reviewed labels to train or validate one, and the career labels are weak title-derived labels.
- Proficiency is ignored: a skill is present or not. The scale is a decision for later.
- No user constraints (location, budget, weekly time) and no negation handling.
- Graduate profile and recommendation tables are not in PostgreSQL yet. They hold user data and the audit trail, so they must survive the full-refresh `load.sql`; they arrive with the Phase 7 API. Only `curated.career_skills` was added (verified on PostgreSQL 16).
- Everything ran on 30 synthetic postings.
