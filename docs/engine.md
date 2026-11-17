# Engine reference

## Governed taxonomy
`data/external/skill_taxonomy.csv` holds 70 canonical skills (`canonical_name`, `display_name`, `category`,
`aliases`, `related_skills`). `data/external/career_taxonomy.csv` holds nine careers (CA01–CA09) with title
keywords; `data/reference/career_profiles.csv` holds skill importance weights (0–1). All three are validated
on load (unknown skill references raise). Edit the CSVs, not the code.

## Extraction (`src/nlp/hybrid_skill_extractor.py`)
Normalise text → phrase match (spaCy `PhraseMatcher` if installed, otherwise an equivalent regex matcher; both
produce identical results in tests) → alias→canonical → longest-span overlap resolution → one record per
(document, skill) with matched text, context, method and deterministic confidence (0.95 exact, 0.85 alias; not a
probability). Ambiguous 1–2 letter terms (`go`, `r`) never match alone.

## Scoring (`src/recommendation/recommendation_engine.py`)
```
coverage          = Σ weight(owned required) / Σ weight(required)        (related skill = 0.5 credit in the web analysis)
market_adjustment = min(1, mean(demand_rate of required) × 5)            (only when observed demand data exist)
match             = 0.85 × coverage + 0.15 × market_adjustment           (= coverage when no demand data)
gap_priority      = importance × (0.75 + 0.50 × demand_rate)
```
The web analysis (`src/service/workspace.py`) applies the same formulas to (a) a matched career profile, or (b)
requirements extracted from a posting. Free-text skills outside the taxonomy are matched by normalised phrase,
CV text, and token overlap (partial credit at Jaccard ≥ 0.5).

## Roadmap generation
Top six gaps by priority → learning resources (free first, from `learning_resources.csv`) → weeks estimate from
catalogue hours at 8 h/week → portfolio project from `project_recommendations.csv`.

## Coach
Deterministic and grounded in the user's analysis. CV bullets are templates with `[placeholders]` so no
achievement or metric is invented. If `LLM_BASE_URL` is set, `/api/v1/chat` forwards to that OpenAI-compatible
endpoint and falls back to the deterministic coach on any error.
