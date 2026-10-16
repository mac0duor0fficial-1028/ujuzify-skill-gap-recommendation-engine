"""Recommend careers and show the skill gap for one graduate profile (Phase 5).

    python src/recommendation/recommend.py                       # uses the demo profile
    python src/recommendation/recommend.py --profile my.json --target CA03
    python src/recommendation/recommend.py --expect-top CA01     # exit 2 if the top career differs

Reads ``data/processed/career_skills.csv`` (build it with build_career_skills.py) and a
profile JSON (see data/reference/demo_profile.json). Writes every ranked career to
``data/processed/recommendations.csv`` with ``model_version`` and ``created_at``
(explainability rule 5) and the full explanation to ``data/processed/recommendations.json``.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etl.common import (  # noqa: E402
    CAREER_SKILL_COLUMNS, CAREER_SKILLS_CSV, RECOMMENDATION_COLUMNS, RECOMMENDATIONS_CSV,
    EtlError, read_csv, write_csv,
)
from src.nlp.skill_dictionary import SkillDictionary  # noqa: E402
from src.recommendation.engine import (  # noqa: E402
    DATA_LIMITS, DISCLAIMER, MODEL_VERSION, MODELS, PREREQUISITES_CSV, gap_analysis,
    load_prerequisites, recommend, resolve_skills,
)
from src.recommendation.profiles import profiles_from_career_skill_rows  # noqa: E402

DEFAULT_PROFILE = ROOT / "data" / "reference" / "demo_profile.json"
RECOMMENDATIONS_JSON = ROOT / "data" / "processed" / "recommendations.json"


def load_profile(path: Path | str) -> dict:
    prof = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("profile_id", "skills"):
        if key not in prof:
            raise ValueError(f"profile file is missing '{key}'")
    if not isinstance(prof["skills"], list):
        raise ValueError("profile 'skills' must be a list of skill names")
    return prof


def run(profile: dict, career_skill_rows: list[dict], *, model: str = MODEL_VERSION,
        target: str | None = None, top: int = 5, prerequisites_path: Path | str | None = None,
        now: datetime | None = None, dictionary: SkillDictionary | None = None) -> dict:
    """Compute recommendations and the gap report. Returns a JSON-serialisable result dict."""
    dictionary = dictionary or SkillDictionary.load()
    profiles = profiles_from_career_skill_rows(career_skill_rows)
    if not profiles:
        raise EtlError("career_skills.csv has no career profiles")
    skills, unknown = resolve_skills(profile["skills"], dictionary)
    if not skills:
        raise ValueError("none of the profile's skills are in the skill taxonomy; nothing to match")
    prereq = load_prerequisites(prerequisites_path or PREREQUISITES_CSV, dictionary)
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    matches = recommend(skills, profiles, dictionary, model)
    target_id = target or profile.get("target_career") or matches[0].career_id
    if target_id not in profiles:
        raise ValueError(f"target career {target_id!r} has no profile (careers with data: {sorted(profiles)})")
    gap = gap_analysis(skills, profiles[target_id], dictionary, prereq)
    return {
        "profile_id": profile["profile_id"],
        "model_version": model,
        "created_at": created,
        "recognized_skills": sorted(dictionary.skills[s].display_name for s in skills),
        "unrecognized_skills": unknown,
        "target_career": target_id,
        "target_source": "requested" if (target or profile.get("target_career")) else "top recommendation",
        "recommendations": [asdict(m) for m in matches],
        "top_n": top,
        "gap": asdict(gap),
        "disclaimer": DISCLAIMER,
        "data_limits": DATA_LIMITS,
    }


def recommendation_rows(result: dict) -> list[dict]:
    rows = []
    for m in result["recommendations"]:
        rows.append({
            "recommendation_id": f"{result['profile_id']}:{result['model_version']}:{m['career_id']}:{result['created_at']}",
            "profile_id": result["profile_id"], "career_id": m["career_id"], "rank": m["rank"],
            "score": f"{m['score']:.4f}", "model_version": result["model_version"],
            "created_at": result["created_at"], "matched_skills": len(m["matched"]),
            "missing_skills": len(m["missing"]), "postings_in_career": m["postings"],
            "low_sample": m["low_sample"],
        })
    return rows


def render(result: dict) -> str:
    out = [f"Profile {result['profile_id']}: {', '.join(result['recognized_skills'])}"]
    if result["unrecognized_skills"]:
        out.append(f"  Not recognised (ignored): {', '.join(result['unrecognized_skills'])}")
    out.append(f"\nCareer matches by compatibility score (model {result['model_version']}):")
    for m in result["recommendations"][: result["top_n"]]:
        flag = f"  [low sample: {m['postings']} postings]" if m["low_sample"] else f"  [{m['postings']} postings]"
        if m["tied_with"]:
            flag += f"  [tied with {m['tied_with']} other career(s): order among them is arbitrary]"
        out.append(f"{m['rank']}. {m['career_name']} - {m['score']:.0%} compatibility{flag}")
        matched = ", ".join(f"{e['display_name']} ({e['frequency']:.0%})" for e in m["matched"][:4]) or "none"
        missing = ", ".join(f"{e['display_name']} ({e['frequency']:.0%})" for e in m["missing"][:3]) or "none"
        out.append(f"   Matched: {matched}")
        out.append(f"   Potential gaps: {missing}")
    gap = result["gap"]
    out.append(f"\nSkill gap for {gap['career_name']} ({result['target_source']}):")
    out.append("  Already have: " + (", ".join(e["display_name"] for e in gap["have"]) or "none"))
    if gap["missing"]:
        out.append("  Priority: " + " -> ".join(g["display_name"] for g in gap["missing"]))
        for g in gap["missing"][:6]:
            out.append(f"    {g['priority']}. {g['display_name']}: {g['reason']}")
    else:
        out.append("  No gaps against this career's sampled requirements.")
    out.append("  (Learning resources for each gap arrive in Phase 6.)")
    out.append(f"\n{result['disclaimer']}\n{result['data_limits']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Career recommendations and skill gap for a profile.")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE))
    ap.add_argument("--career-skills", default=str(CAREER_SKILLS_CSV))
    ap.add_argument("--model", default=MODEL_VERSION, choices=sorted(MODELS))
    ap.add_argument("--target", default=None, help="career_id for the gap analysis (default: top match)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--prerequisites", default=str(PREREQUISITES_CSV))
    ap.add_argument("--out", default=str(RECOMMENDATIONS_CSV))
    ap.add_argument("--json", default=str(RECOMMENDATIONS_JSON))
    ap.add_argument("--expect-top", default=None, help="career_id the top match must be (exit 2 if not)")
    args = ap.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        rows = read_csv(Path(args.career_skills), CAREER_SKILL_COLUMNS)
        result = run(profile, rows, model=args.model, target=args.target, top=args.top,
                     prerequisites_path=args.prerequisites)
        write_csv(Path(args.out), RECOMMENDATION_COLUMNS, recommendation_rows(result))
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    except (EtlError, ValueError, OSError, json.JSONDecodeError) as e:
        print(f"RECOMMENDATION FAILED: {e}")
        return 1
    print(render(result))
    top = result["recommendations"][0]["career_id"]
    if args.expect_top and top != args.expect_top:
        print(f"\nEXPECTATION FAILED: top career is {top}, expected {args.expect_top}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
