# Phase 2 — Data Acquisition

Scope: source registry, canonical raw job schema, ingestion CLI, raw-data validation, and the skill/career dictionaries the later phases build on. Skill extraction (Phase 4) and PostgreSQL loading (Phase 3) are not part of this phase.

## Run order (Phase 2 gate)
```bash
python src/ingestion/job_api.py          # fetch + normalize -> data/raw/jobs_<source>_<UTC>.jsonl (+ .manifest.json)
python src/ingestion/validate_jobs.py    # validate latest file per source; writes data/interim/job_validation_report.json
pytest                                   # all Phase 2 tests must pass
```
`job_api.py` defaults to the offline `sample_fixture` source. Use `--list-sources` to see what is registered and whether it is `ready` or `blocked`.

## Source registry (`data/external/source_registry.json`)
Each source declares: `type` (`fixture` | `http_json`), `enabled`, `terms_reviewed`, `terms_url`, `is_synthetic`, `redistribution_allowed`, `country`, and a `config` block. The registry is validated on every load.

Rules enforced in code:
- A source runs only if it is registered, `enabled` **and** `terms_reviewed`. `enabled=true` with `terms_reviewed=false` is a registry error.
- A real (non-synthetic) source marked `terms_reviewed` must have a `terms_url`.
- `http_json` endpoint details (path, auth header/scheme, pagination, records path, field map) live in the registry, not in code. Credentials come from environment variables named in the config and are sent as a header, never in the URL or logs.
- Non-HTTPS URLs are refused (except localhost, for tests). Requests retry on 429/5xx with backoff and honor `Retry-After`; pages are spaced by `min_interval_s`.

### Adding a real source
1. Read the provider's terms and API documentation. Do not use anything that bypasses terms or access controls.
2. Copy the `job_opportunities_api` entry, set a new `source_id`, fill `terms_url`, and correct `path`, auth, pagination, `records_path` and `field_map` against the provider's documentation (the shipped values are placeholders).
3. Put credentials in `.env`, then set `terms_reviewed=true` and `enabled=true`.
4. Run ingestion with a small `--limit` first and inspect the output and validation report.
5. Check `redistribution_allowed` before committing or sharing any raw data. `data/raw/` is git-ignored by default.

## Canonical raw schema (`src/ingestion/raw_schema.py`, version 1.0)
One JSON object per line. All 17 keys are always present; unknown optional values are `null`.

| Field | Required | Notes |
|---|---|---|
| schema_version | yes | `"1.0"` |
| job_id | yes | `<source_id>:<source_job_id>`, unique per corpus |
| source_id, source_job_id | yes | provenance |
| source_url | no | |
| title, description | yes | description is plain text (HTML stripped) |
| company, location | no | as published |
| country | yes | ISO alpha-2, default from the registry |
| employment_type, experience_required, education_required | no | free text as published |
| date_posted | no | UTC `YYYY-MM-DDTHH:MM:SSZ`; `DD/MM/YYYY` inputs are read day-first |
| retrieved_at | yes | UTC ISO 8601 |
| content_hash | yes | sha256 of lowercased, whitespace-collapsed title, company, location, description (joined by `\x1f`) |
| is_synthetic | yes | `true` for fixtures |

Within a run, records are dropped if the `job_id` or `content_hash` was already seen (counted in the manifest). Cross-run and cross-source deduplication belongs to Phase 3 ETL.

## Validation (`src/ingestion/validate_jobs.py`)
Errors (exit 1): unknown or missing fields, wrong types, `job_id` not matching its parts, unregistered source, `is_synthetic` disagreeing with the registry, description under 20 characters or still containing HTML, `content_hash` not matching content, malformed timestamps, `date_posted` after `retrieved_at`, duplicate `job_id`, no raw files found.
Warnings: missing company or date, short description, no dictionary skill in a description, reposts sharing a content hash.

The report also gives dictionary-lookup coverage and title-to-career counts. These are **lookup coverage, not extraction accuracy**; measuring accuracy requires the manually reviewed gold set described in `docs/evaluation_strategy.md`.

## Dictionaries (`src/nlp/skill_dictionary.py`)
- `SkillDictionary`: loads `skill_taxonomy.csv`; `lookup(term)` resolves one explicit skill (including bare `r` and `go`); `scan(text)` does longest-phrase matching and deliberately skips bare `r` and `go` in prose (Phase 1 governance; Phase 4 owns context handling).
- `CareerTaxonomy`: loads `career_taxonomy.csv` and `career_title_aliases.csv`; `match_title(title)` returns the career for the longest matching title alias, or `None` when nothing matches or two careers tie.
- `normalize()` is identical to `scripts/validate_taxonomies.py` (a test enforces parity).
- Title aliases are an authored starting list, like the skill vocabulary. Review them against real postings once available.

## Synthetic fixture
`data/external/sample_jobs_synthetic.json` holds 30 fictional postings (3 per career, 2 reposts, 1 sparse record with a day-first date). It exists to test the pipeline offline. Statistics from it are meaningless for the Kenyan labour market, and records carry `is_synthetic=true` so they can be excluded downstream.

## Known limits
- The `job_opportunities_api` entry is an unconfigured template: no real provider endpoint, authentication scheme or terms have been verified.
- The live HTTP path is tested against a local test server only, not a real provider.
- Phase 2 tests: 5 test functions (README gates are cumulative: 10 after Phase 5).
