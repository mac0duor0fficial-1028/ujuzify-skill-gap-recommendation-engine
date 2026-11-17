"""Batch recommendations for graduate profiles in a CSV file.

Input columns: profile_id, education, experience_years, interests, skills
Outputs: data/processed/career_recommendations.csv and skill_gap_recommendations.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src import config
from src.recommendation.recommendation_engine import get_engine
from src.taxonomy import get_taxonomy


def run(profiles_csv: Path, out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = Path(out_dir or config.PROCESSED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not Path(profiles_csv).exists():
        raise FileNotFoundError(f"Profile file not found: {profiles_csv}")
    tax, engine = get_taxonomy(), get_engine(reload=True)
    rec_rows, gap_rows = [], []
    with open(profiles_csv, newline="", encoding="utf-8") as f:
        for p in csv.DictReader(f):
            owned, _ = tax.parse_skill_list(p.get("skills", ""))
            ranked = engine.rank_careers(owned)
            for rank, m in enumerate(ranked, 1):
                rec_rows.append({"profile_id": p["profile_id"], "rank": rank, "career_id": m.career_id,
                                 "career_name": m.career_name, "match_score": m.match_score,
                                 "skill_coverage": m.skill_coverage, "confidence": m.confidence,
                                 "matched_skills": "|".join(m.matched_skills),
                                 "missing_skills": "|".join(m.missing_skills), "explanation": m.explanation,
                                 "model_version": config.MODEL_VERSION})
            for g in engine.skill_gaps(ranked[0].career_id, owned):
                gap_rows.append({"profile_id": p["profile_id"], "career_id": ranked[0].career_id,
                                 "skill_id": g.skill_id, "importance": g.importance,
                                 "demand_rate": "" if g.demand_rate is None else g.demand_rate,
                                 "priority": g.priority, "reason": g.reason})
    rec_path, gap_path = out_dir / "career_recommendations.csv", out_dir / "skill_gap_recommendations.csv"
    for path, rows in ((rec_path, rec_rows), (gap_path, gap_rows)):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["profile_id"])
            w.writeheader()
            w.writerows(rows)
    return rec_path, gap_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default=str(config.DATA_DIR / "graduate_profiles" / "example_profile.csv"))
    a = ap.parse_args()
    print("wrote", *run(Path(a.profiles)))
