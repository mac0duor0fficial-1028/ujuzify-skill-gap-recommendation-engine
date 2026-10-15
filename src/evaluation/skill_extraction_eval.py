"""Evaluate skill extraction against the development gold set (Phase 4).

    python src/evaluation/skill_extraction_eval.py

Metrics follow docs/evaluation_strategy.md section 2: sets of (doc_id, skill_id) pairs,
precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2PR/(P+R), micro-averaged. The
Phase 2 dictionary scan (baseline) and the Phase 4 rule extractor are scored on the same
snippets, as the strategy requires of any new method.

Two gold files are scored:

* ``dev``: written first, but the extractor's context-rule cue lists were then designed
  with these cases in view, so a high score here is expected and is NOT evidence of quality.
* ``challenge``: written after the rules were frozen, with different phrasing and cases
  chosen to break the rules. Results were reported as they came out, not tuned to.

CAVEAT (evaluation_strategy section 5): both are small sets written by the extractor's
own author, so they validate that the pipeline and rules work and expose known failure
modes. They must NOT be quoted as real-world accuracy. A real benchmark needs several
hundred randomly sampled Kenyan postings with independent reviewers.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.nlp.skill_dictionary import SkillDictionary  # noqa: E402
from src.nlp.skill_extractor import SkillExtractor  # noqa: E402

GOLD_DEV_CSV = ROOT / "data" / "evaluation" / "skill_gold_dev.csv"
GOLD_CHALLENGE_CSV = ROOT / "data" / "evaluation" / "skill_gold_challenge.csv"
REPORT_JSON = ROOT / "data" / "interim" / "skill_eval_report.json"
GOLD_COLUMNS = ["doc_id", "case_tag", "text", "skills"]


def load_gold(path: Path | str, dictionary: SkillDictionary) -> list[dict]:
    """Read the gold CSV; labels are canonical skill names, validated against the taxonomy."""
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != GOLD_COLUMNS:
            raise ValueError(f"gold file columns must be {GOLD_COLUMNS}, found {reader.fieldnames}")
        rows = list(reader)
    seen: set[str] = set()
    gold = []
    for r in rows:
        if r["doc_id"] in seen:
            raise ValueError(f"duplicate gold doc_id {r['doc_id']}")
        seen.add(r["doc_id"])
        ids: set[str] = set()
        for name in filter(None, r["skills"].split("|")):
            skill = dictionary.by_canonical.get(name)
            if skill is None:
                raise ValueError(f"{r['doc_id']}: unknown skill '{name}' in gold labels")
            ids.add(skill.skill_id)
        gold.append({"doc_id": r["doc_id"], "case_tag": r["case_tag"], "text": r["text"], "skills": ids})
    return gold


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def evaluate(gold: list[dict], predict: Callable[[str], list[str]]) -> dict:
    """Micro-averaged pair-level scores plus a per-document error list."""
    tp = fp = fn = 0
    errors = []
    by_tag: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in gold:
        pred = set(predict(g["text"]))
        t, f_p, f_n = len(pred & g["skills"]), pred - g["skills"], g["skills"] - pred
        tp, fp, fn = tp + t, fp + len(f_p), fn + len(f_n)
        by_tag[g["case_tag"]][0] += t
        by_tag[g["case_tag"]][1] += len(f_p)
        by_tag[g["case_tag"]][2] += len(f_n)
        if f_p or f_n:
            errors.append({"doc_id": g["doc_id"], "text": g["text"],
                           "false_positives": sorted(f_p), "false_negatives": sorted(f_n)})
    out = prf(tp, fp, fn)
    out["documents"] = len(gold)
    out["errors"] = errors
    out["by_tag"] = {k: prf(*v) for k, v in sorted(by_tag.items())}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score skill extraction on the gold sets.")
    ap.add_argument("--dev", default=str(GOLD_DEV_CSV))
    ap.add_argument("--challenge", default=str(GOLD_CHALLENGE_CSV))
    ap.add_argument("--report", default=str(REPORT_JSON))
    args = ap.parse_args(argv)
    try:
        sd = SkillDictionary.load()
        sets = {"dev (rules designed with these in view)": load_gold(args.dev, sd),
                "challenge (written after rules were frozen)": load_gold(args.challenge, sd)}
    except (OSError, ValueError) as e:
        print(f"EVALUATION FAILED: {e}")
        return 1
    extractor = SkillExtractor(sd)
    report: dict = {}
    print("SKILL EXTRACTION EVALUATION")
    for set_name, gold in sets.items():
        results = {
            "dictionary_scan_v0 (baseline)": evaluate(gold, sd.skill_ids_in),
            "rule_extractor_v1": evaluate(gold, extractor.extract_ids),
        }
        report[set_name] = results
        n_pairs = sum(len(g["skills"]) for g in gold)
        print(f"\n  {set_name}: {len(gold)} snippets, {n_pairs} gold pairs")
        print(f"    {'method':32s} {'precision':>9s} {'recall':>7s} {'f1':>6s} {'tp':>4s} {'fp':>4s} {'fn':>4s}")
        for name, r in results.items():
            print(f"    {name:32s} {r['precision']:9.3f} {r['recall']:7.3f} {r['f1']:6.3f} "
                  f"{r['tp']:4d} {r['fp']:4d} {r['fn']:4d}")
        for e in results["rule_extractor_v1"]["errors"]:
            print(f"    error {e['doc_id']}: FP={e['false_positives']} FN={e['false_negatives']} "
                  f"| {e['text'][:60]!r}")
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n  NOTE: small sets written by the extractor's author. Not real-world accuracy.")
    print(f"  report: {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
