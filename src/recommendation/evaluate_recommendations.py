"""Hit@1, Hit@3 and MRR against a labelled gold file (profile_id, acceptable_career_ids).

The bundled gold file is a development fixture that validates the evaluation wiring; it is
NOT evidence of real-world accuracy.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src import config
from src.recommendation.recommendation_engine import get_engine
from src.taxonomy import get_taxonomy


def evaluate(profiles_csv: Path, gold_csv: Path, out_csv: Path | None = None) -> dict[str, float]:
    tax, engine = get_taxonomy(), get_engine()
    gold: dict[str, set[str]] = {}
    with open(gold_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            gold[r["profile_id"]] = {c for c in r["acceptable_career_ids"].split("|") if c}
    hit1 = hit3 = rr = n = 0.0
    with open(profiles_csv, newline="", encoding="utf-8") as f:
        for p in csv.DictReader(f):
            if p["profile_id"] not in gold:
                continue
            owned, _ = tax.parse_skill_list(p["skills"])
            ranked = [m.career_id for m in engine.rank_careers(owned)]
            ok = gold[p["profile_id"]]
            n += 1
            hit1 += ranked[0] in ok
            hit3 += any(c in ok for c in ranked[:3])
            for i, c in enumerate(ranked, 1):
                if c in ok:
                    rr += 1 / i
                    break
    if n == 0:
        raise ValueError("No overlapping profile_ids between profiles and gold labels")
    metrics = {"n_profiles": n, "hit_at_1": hit1 / n, "hit_at_3": hit3 / n, "mrr": rr / n}
    out_csv = Path(out_csv or config.EVALUATION_DIR / "career_recommendation_metrics.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for k, v in metrics.items():
            w.writerow([k, round(v, 4)])
    return metrics


if __name__ == "__main__":
    print(evaluate(config.DATA_DIR / "graduate_profiles" / "example_profile.csv",
                   config.EVALUATION_DIR / "career_recommendation_gold.csv"))
