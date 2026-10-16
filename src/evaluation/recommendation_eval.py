"""Evaluate career recommendation models (Phase 5).

    python src/evaluation/recommendation_eval.py

Metrics follow docs/evaluation_strategy.md section 1: Precision@K, Recall@K, NDCG@K (binary
relevance), MRR, Hit@1 and Hit@3, averaged over profiles. All four models are scored on
identical inputs: popularity_v0, keyword_overlap_v0, tfidf_cosine_v0 (the baselines) and
weighted_coverage_v1 (the engine).

Two evaluations are run:

1. **Gold profiles** (data/evaluation/profile_gold_dev.csv): hand-written graduate profiles
   with acceptable careers. Written by the engine's author before the engine ran.
2. **Leave-one-out postings**: each labeled posting's skills become a pseudo-profile whose
   acceptable career is the posting's (weak, title-derived) label, and career profiles are
   rebuilt *without that posting*. Run with all of the posting's skills and with a
   deterministic 60% subset to mimic a graduate who knows only part of a role's skills.

CAVEATS (evaluation_strategy section 5). With the shipped synthetic fixture both are
circular: the fixture's skills were generated per career, and the gold profiles were written
by the same author from the same ideas, so high scores validate the evaluation pipeline,
not real-world accuracy. Real evaluation needs real Kenyan graduate profiles with acceptable
careers labelled by people who did not build the engine. On real postings the leave-one-out
test becomes a meaningful sanity check, but its labels still come from titles.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etl.common import (  # noqa: E402
    CAREER_SKILL_COLUMNS, CAREER_SKILLS_CSV, JOB_SKILL_COLUMNS, JOB_SKILLS_CSV, LABEL_COLUMNS,
    LABELS_CSV, STAGING_COLUMNS, STAGING_CSV, EtlError, read_csv,
)
from src.nlp.skill_dictionary import CAREERS_CSV, SkillDictionary  # noqa: E402
from src.recommendation.engine import MODEL_VERSION, MODELS, rank_careers  # noqa: E402
from src.recommendation.profiles import (  # noqa: E402
    CorpusCounts, Posting, postings_from_rows, profiles_from_career_skill_rows,
)

GOLD_PROFILES_CSV = ROOT / "data" / "evaluation" / "profile_gold_dev.csv"
REPORT_JSON = ROOT / "data" / "interim" / "recommendation_eval_report.json"
GOLD_COLUMNS = ["profile_id", "skills", "acceptable_careers", "note"]
K = 3


# ------------------------------------------------------------------ metrics
def precision_at_k(ranked: list[str], relevant: set, k: int) -> float:
    return sum(1 for c in ranked[:k] if c in relevant) / k


def recall_at_k(ranked: list[str], relevant: set, k: int) -> float:
    return sum(1 for c in ranked[:k] if c in relevant) / len(relevant)


def ndcg_at_k(ranked: list[str], relevant: set, k: int) -> float:
    dcg = sum(1 / math.log2(i + 1) for i, c in enumerate(ranked[:k], 1) if c in relevant)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return dcg / ideal if ideal else 0.0


def reciprocal_rank(ranked: list[str], relevant: set) -> float:
    for i, c in enumerate(ranked, 1):
        if c in relevant:
            return 1 / i
    return 0.0


def hit_at_k(ranked: list[str], relevant: set, k: int) -> float:
    return float(any(c in relevant for c in ranked[:k]))


def summarize(rankings: dict[str, list[str]], gold: dict[str, set]) -> dict:
    """Mean metrics over profiles that have at least one acceptable career."""
    ids = [p for p in rankings if gold.get(p)]
    if not ids:
        raise ValueError("no profiles with acceptable careers to evaluate")
    mean = lambda f: round(sum(f(rankings[p], gold[p]) for p in ids) / len(ids), 4)  # noqa: E731
    return {
        "profiles": len(ids),
        "hit@1": mean(lambda r, g: hit_at_k(r, g, 1)),
        "hit@3": mean(lambda r, g: hit_at_k(r, g, 3)),
        "mrr": mean(reciprocal_rank),
        f"precision@{K}": mean(lambda r, g: precision_at_k(r, g, K)),
        f"recall@{K}": mean(lambda r, g: recall_at_k(r, g, K)),
        f"ndcg@{K}": mean(lambda r, g: ndcg_at_k(r, g, K)),
    }


# ------------------------------------------------------------------ gold profiles
def load_gold_profiles(path: Path | str, dictionary: SkillDictionary,
                       career_ids: set[str]) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != GOLD_COLUMNS:
            raise ValueError(f"gold profile columns must be {GOLD_COLUMNS}, found {reader.fieldnames}")
        rows = list(reader)
    out, seen = [], set()
    for r in rows:
        if r["profile_id"] in seen:
            raise ValueError(f"duplicate gold profile_id {r['profile_id']}")
        seen.add(r["profile_id"])
        ids = set()
        for name in filter(None, r["skills"].split("|")):
            sk = dictionary.by_canonical.get(name)
            if sk is None:
                raise ValueError(f"{r['profile_id']}: unknown skill '{name}'")
            ids.add(sk.skill_id)
        acceptable = set(filter(None, r["acceptable_careers"].split("|")))
        unknown = acceptable - career_ids
        if not acceptable or unknown:
            raise ValueError(f"{r['profile_id']}: acceptable_careers must be known career ids, got {sorted(acceptable)}")
        out.append({"profile_id": r["profile_id"], "skills": frozenset(ids), "acceptable": acceptable})
    return out


def evaluate_gold_profiles(gold: list[dict], profiles: dict) -> dict:
    """model name -> metrics, plus the per-profile top-ranked career for inspection."""
    acceptable = {g["profile_id"]: g["acceptable"] for g in gold}
    report = {}
    for name, fn in MODELS.items():
        rankings = {g["profile_id"]: rank_careers(fn(g["skills"], profiles), profiles) for g in gold}
        report[name] = summarize(rankings, acceptable)
        report[name]["misses_at_1"] = sorted(p for p, r in rankings.items() if r[0] not in acceptable[p])
    return report


# ------------------------------------------------------------------ leave-one-out postings
def _keep(job_id: str, skill_id: str, fraction: float) -> bool:
    h = int(hashlib.sha1(f"{job_id}|{skill_id}".encode()).hexdigest()[:8], 16) / 2**32
    return h < fraction


def evaluate_leave_one_out(postings: list[Posting], career_names: dict[str, str],
                           keep_fraction: float = 1.0, min_postings: int = 30) -> dict:
    counts = CorpusCounts(postings, career_names, min_postings)
    rankings: dict[str, list[str]] = {name: {} for name in MODELS}
    gold: dict[str, set] = {}
    skipped = 0
    for p in postings:
        skills = frozenset(s for s in p.skills if _keep(p.job_id, s, keep_fraction))
        profiles = counts.profiles(exclude=p)
        if not skills or p.career_id not in profiles:
            skipped += 1     # nothing to rank with, or the held-out career has no other posting
            continue
        gold[p.job_id] = {p.career_id}
        for name, fn in MODELS.items():
            rankings[name][p.job_id] = rank_careers(fn(skills, profiles), profiles)
    report = {name: summarize(r, gold) for name, r in rankings.items()} if gold else {}
    return {"keep_fraction": keep_fraction, "skipped_postings": skipped, "models": report}


# ------------------------------------------------------------------ CLI
def _print_table(title: str, models: dict) -> None:
    n = next(iter(models.values()))["profiles"]
    print(f"\n  {title} ({n} profiles)")
    cols = ["hit@1", "hit@3", "mrr", f"precision@{K}", f"recall@{K}", f"ndcg@{K}"]
    print(f"    {'model':24s}" + "".join(f"{c:>13s}" for c in cols))
    for name, m in models.items():
        print(f"    {name:24s}" + "".join(f"{m[c]:13.3f}" for c in cols))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate career recommendation models.")
    ap.add_argument("--gold", default=str(GOLD_PROFILES_CSV))
    ap.add_argument("--career-skills", default=str(CAREER_SKILLS_CSV))
    ap.add_argument("--staging", default=str(STAGING_CSV))
    ap.add_argument("--labels", default=str(LABELS_CSV))
    ap.add_argument("--job-skills", default=str(JOB_SKILLS_CSV))
    ap.add_argument("--report", default=str(REPORT_JSON))
    args = ap.parse_args(argv)
    try:
        sd = SkillDictionary.load()
        profiles = profiles_from_career_skill_rows(read_csv(Path(args.career_skills), CAREER_SKILL_COLUMNS))
        gold = load_gold_profiles(args.gold, sd, set(profiles))
        report = {"gold_profiles": evaluate_gold_profiles(gold, profiles)}
        postings = postings_from_rows(read_csv(Path(args.staging), STAGING_COLUMNS),
                                      read_csv(Path(args.labels), LABEL_COLUMNS),
                                      read_csv(Path(args.job_skills), JOB_SKILL_COLUMNS))
        with Path(CAREERS_CSV).open(newline="", encoding="utf-8") as fh:
            names = {r["career_id"]: r["career_name"] for r in csv.DictReader(fh)}
        report["leave_one_out"] = [evaluate_leave_one_out(postings, names, f) for f in (1.0, 0.6)]
    except (EtlError, ValueError, OSError) as e:
        print(f"EVALUATION FAILED: {e}")
        return 1
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("CAREER RECOMMENDATION EVALUATION")
    _print_table("gold profiles (author-written)", report["gold_profiles"])
    misses = report["gold_profiles"][MODEL_VERSION]["misses_at_1"]
    print(f"    {MODEL_VERSION} top-1 misses: {misses or 'none'}")
    for loo in report["leave_one_out"]:
        if loo["models"]:
            _print_table(f"leave-one-out postings, {loo['keep_fraction']:.0%} of each posting's skills", loo["models"])
    print("\n  NOTE: on the synthetic fixture these evaluations are circular (see module docstring);")
    print("  they validate the evaluation pipeline, not real-world accuracy.")
    print(f"  report: {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
