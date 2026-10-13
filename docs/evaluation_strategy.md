# Evaluation Strategy

## 1. Career recommendation
For a profile with a set of relevant (acceptable) careers `R` and a ranked list of recommendations:

| Metric | Definition |
|---|---|
| Precision@K | (relevant careers in top K) / K |
| Recall@K | (relevant careers in top K) / \|R\| |
| NDCG@K | DCG@K / ideal DCG@K, where DCG@K = sum over ranks i=1..K of rel_i / log2(i + 1) |
| MRR | mean over profiles of 1 / rank of the first relevant career |

Phase 5 additionally reports **Hit@1** and **Hit@3** (is at least one acceptable career at rank 1 / in the top 3), plus MRR, because the early gold set has few labels per profile.

## 2. Skill extraction
Compared as sets of `(job_id, skill_id)` pairs against a manually reviewed gold set:
- Precision = TP / (TP + FP)
- Recall = TP / (TP + FN)
- F1 = 2 * precision * recall / (precision + recall)

Manually reviewed examples are required; extracted skills are never assumed correct. A serious benchmark needs several hundred randomly sampled Kenyan postings, ideally with independent reviewers and adjudication.

## 3. Skill-gap ranking
- Compare recommended priority against skill frequency in job postings.
- Compare against role-specific importance.
- Use expert/manual relevance labels where available.

## 4. Baselines (in increasing sophistication)
1. Frequency-based career recommendation
2. Keyword-based skill matching
3. TF-IDF / cosine similarity
4. Embedding-based similarity
5. Hybrid or learning-to-rank model

Each new method must beat the previous baselines on the same held-out evaluation data to justify its added complexity.

## 5. Caveats
- Small development gold sets only validate that the evaluation pipeline works; they must not be reported as real-world accuracy.
- Held-out evaluation needs real Kenyan graduate profiles with target or acceptable career labels.
- Compatibility scores are not calibrated probabilities (see `docs/explainability_requirements.md`).
