# Phase 4 — NLP Skill Extraction

Scope: link job-posting text to the governed skill taxonomy, record the evidence for each link, and measure the extractor. Recommendation logic (Phase 5) is not part of this phase.

## Run order (Phase 4 gate)
```bash
python src/nlp/extract_job_skills.py                                   # staging_jobs.csv -> data/processed/job_skills.csv
python src/etl/build_skill_demand.py --job-skills data/processed/job_skills.csv   # demand from extractor output
python src/evaluation/skill_extraction_eval.py                         # scores both gold sets
pytest                                                                 # 9 tests (5 + 2 + 2)
```
Without `--job-skills`, `build_skill_demand.py` still uses the Phase 2 dictionary scan (`method=dictionary_scan_v0`), so earlier gates are unchanged. The `method` column always says which one produced the numbers.

## The extractor (`src/nlp/skill_extractor.py`, `rule_extractor_v1`)
Rule-based, standard library only, and it never outputs a skill that is not in `skill_taxonomy.csv`.

1. **Candidates:** the Phase 2 longest-phrase dictionary match (aliases, casing and punctuation behave as in the dictionary; a test checks tokenizer parity).
2. **Remove non-skill uses of everyday words.** Single-word matches of risky forms are checked in context: `excel` (verb: "excel at", "to excel"), `react` ("react quickly"), `spark` ("spark innovation"), `monitoring`, `networking`, `agile`, `containers`, `documentation`, `torch` (these need a technical cue word within a few tokens). A risky word without a cue is still accepted if it sits in a list next to an already accepted skill ("Docker, Azure, monitoring, Bash scripting"). Multi-word aliases (`microsoft excel`) are always accepted.
3. **Add bare `R` and `Go`.** Accepted only as exact-case `R` / `Go`, and only after in/with/using/of/for or in a list next to another accepted skill. `R&D` is excluded.
4. **Requirement level and importance (heuristic).** An explicit "a plus / an advantage / nice to have" in the sentence gives `preferred` (importance 0.5); "must / required / essential" gives `required`; otherwise `unspecified`. Required and unspecified score 1.0. A sentence with both kinds of cue is `unspecified`. Importance is not a measured weight.

Every `job_skills.csv` row carries the `evidence` text (raw words from the posting), the `rule` that accepted it, and `mentions`.

## Measured behaviour (`skill_extraction_eval.py`)
Two small gold sets, both **written by the extractor's own author**:

| Set | Snippets / pairs | Baseline (dictionary scan) P / R / F1 | rule_extractor_v1 P / R / F1 |
|---|---|---|---|
| dev | 72 / 118 | 0.904 / 0.958 / 0.930 | 1.000 / 1.000 / 1.000 |
| challenge | 43 / 44 | 0.833 / 0.909 / 0.870 | 0.976 / 0.909 / 0.941 |

How to read this:
- **dev is not evidence of quality.** The context rules were designed with those cases in view; a perfect score is expected. It is kept as a regression guard.
- **challenge** was written after the rules were frozen, with different phrasing and cases chosen to break them, and scored once. The gain over the baseline is almost entirely precision (fewer false positives from ordinary words); recall did not improve.
- With 44 gold pairs, one error moves a score by about 2 points. Treat the difference as indicative only.
- **Disclosure:** after that first challenge scoring, one rule was added (list-context acceptance of risky words) because the synthetic fixture showed `monitoring`/`networking` being dropped from skill lists. The challenge results were unchanged by it, and its two new test cases were added to the dev set only.
- **None of this is real-world accuracy.** A defensible benchmark needs several hundred randomly sampled Kenyan postings, at least two independent reviewers, and a reported agreement score (`docs/evaluation_strategy.md`).

### Known failures (pinned in the tests)
| Case | Why |
|---|---|
| "Statistical software (SPSS, Stata, R)" misses `R` | neighbours are tools outside the taxonomy, so no list evidence |
| "Agile ways of working" misses `agile` | no cue word nearby |
| "No prior experience with Java is needed" yields `java` | no negation handling |
| "Dockerized", "Penetration tester" missed | no stemming; the aliases are `docker`, `penetration testing` |

If a rule change fixes or adds one of these, `test_extractor_rules_and_evaluation_harness` fails so the change is made knowingly.

## Gold labelling guideline
Label a skill when the snippet names it in a technical or professional sense, or uses a taxonomy alias in that sense. Do not label incidental ordinary words ("shipping containers"), skills a snippet says are *not* needed, or job titles on their own. Labels are canonical skill names, validated against the taxonomy on load.

## Taxonomy issues noticed (vocabulary decisions left for review, not changed here)
`nosql` maps to MongoDB; `keras` maps to TensorFlow; `collaboration` maps to teamwork (very broad); `documentation` and `access control` are ambiguous words. Real postings will also need many more aliases; recall is bounded by the vocabulary, not only by the rules.

## PostgreSQL
`database/schema.sql` adds `staging.job_skills` and `curated.job_skills` (primary key `(job_id, skill_id)`, foreign keys to postings and skills, importance in (0, 1], requirement level checked). `database/load.sql` loads it. Applied to a local PostgreSQL 16 server: 178 `job_skills` rows from the fixture loaded. This check is not part of `pytest`.

## Limits
- Everything above ran on 30 synthetic postings and author-written snippets, not real Kenyan data.
- No negation, no stemming, no semantic matching; these are the main recall and precision gaps to address if the benchmark shows they matter.
- Skills extracted here are mentions in text. They are not evidence that any graduate has the skill.
