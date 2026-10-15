# Phase 3 — ETL and PostgreSQL

Scope: turn validated raw postings (Phase 2) into a staging table, weak career labels and a skill-demand table, and define the PostgreSQL schema they load into. Proper skill extraction (Phase 4) and the recommendation logic (Phase 5) are not part of this phase.

## Run order (Phase 3 gate)
```bash
python src/ingestion/job_api.py          # Phase 2, if data/raw is empty
python src/etl/build_staging.py          # -> data/interim/staging_jobs.csv
python src/etl/build_labels.py           # -> data/processed/job_labels.csv
python src/etl/build_skill_demand.py     # -> data/processed/skill_demand.csv
pytest                                   # 7 tests (5 Phase 2 + 2 Phase 3)
```
Each script exits 1 with a clear message if its input is missing or invalid. The three CSVs are generated and git-ignored.

## Steps
**1. `build_staging.py`** re-validates every raw file with the Phase 2 validator (any error stops the run and writes nothing), then:
- keeps the most recently retrieved version of each `job_id` (cross-run dedup);
- folds postings with the same `content_hash` into one representative, the one with the earliest `date_posted`, and records the number folded in `repost_count` (cross-source and repost dedup);
- adds `title_norm` and `description_chars`.

Options: `--latest-only` (newest file per source only), `--exclude-synthetic`.

**2. `build_labels.py`** gives each posting a career when its title contains an alias from `data/external/career_title_aliases.csv` (longest alias wins; a tie between careers means no label). Unmatched postings stay in the file with `label_status=unmapped`. The matched alias is recorded as evidence.

> These are **weak labels derived from titles**, not reviewed gold labels. Do not use them to evaluate any model that also reads the title: that would be circular. Gold labels require manual review (see `docs/evaluation_strategy.md`).

**3. `build_skill_demand.py`** counts, for the whole corpus (`scope=all`) and for each career with labeled postings, how many postings mention each skill: `share = postings_with_skill / postings_in_scope`. Skills with no mentions get no row.
- `method=dictionary_scan_v0`: skills come from the Phase 2 dictionary scan, a provisional method with unmeasured precision. Phase 4 supersedes it.
- `low_sample=true` marks scopes with fewer than `--min-n` postings (default 30, an arbitrary rule of thumb; tune it).
- `synthetic_postings_in_scope` shows how many postings came from fixtures.
- These are sample statistics from the acquired corpus, not a ranking of the Kenyan labour market (explainability rule 3).

## PostgreSQL
`database/schema.sql` creates three schemas: `staging` (CSV mirrors), `curated` (typed tables with constraints and foreign keys: `careers`, `skills`, `job_postings`, `job_career_labels`) and `analytics` (`skill_demand`). Raw JSONL stays on disk. Tables for profiles, job skills, career skills, learning resources and recommendations arrive with the phases that produce that data.

```bash
# from the project root; DATABASE_URL as in .env.example
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/schema.sql -f database/load.sql
```
`schema.sql` is idempotent. `load.sql` is a full refresh in one transaction: it truncates and reloads everything, so a failed load leaves the previous data in place. It uses psql `\copy` with paths relative to the directory you run it from, so run it from the project root.

Constraints include ISO country codes, non-empty titles and descriptions, `share` in (0, 1], `postings_with_skill <= postings_in_scope`, and labeled rows requiring a career. A test checks that the staging tables' columns match the CSV headers exactly.

## Verification and limits
- The schema and loader were applied (twice, to check idempotence) to a local PostgreSQL 16 server and the constraints were exercised with bad rows. This is **not** part of `pytest`, so CI without PostgreSQL still passes. Re-run the psql command above on your own database to confirm.
- Everything above ran on the 30-record synthetic fixture. Row counts and shares from it (28 postings, every scope `low_sample`) say nothing about the Kenyan labour market.
- The Phase 3 gate checks the pipeline works, not that its outputs are accurate.
