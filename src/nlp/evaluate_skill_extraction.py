"""Precision / recall / F1 over (job_id, skill_id) pairs against a labelled gold file.

The bundled gold file is a development fixture; it validates the evaluator, not real-world quality.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src import config


def evaluate(pred_csv: Path | None = None, gold_csv: Path | None = None, out_csv: Path | None = None) -> dict:
    pred_csv = Path(pred_csv or config.PROCESSED_DIR / "job_skills_nlp.csv")
    gold_csv = Path(gold_csv or config.EVALUATION_DIR / "skill_extraction_gold.csv")
    out_csv = Path(out_csv or config.EVALUATION_DIR / "skill_extraction_metrics.csv")
    rd = lambda p: {(r["job_id"], r["skill_id"]) for r in csv.DictReader(open(p, newline="", encoding="utf-8"))}  # noqa: E731
    pred, gold = rd(pred_csv), rd(gold_csv)
    gold_jobs = {j for j, _ in gold}
    pred = {p for p in pred if p[0] in gold_jobs}  # score only labelled jobs
    tp, fp, fn = len(pred & gold), len(pred - gold), len(gold - pred)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    m = {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows(m.items())
    return m


if __name__ == "__main__":
    print(evaluate())
