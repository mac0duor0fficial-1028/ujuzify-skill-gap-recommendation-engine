"""Combine ranked careers, gaps, resources and projects into one payload (the /recommend response)."""
from __future__ import annotations

from src import config
from src.product.resource_recommender import rank_projects, rank_resources
from src.recommendation.recommendation_engine import get_engine
from src.taxonomy import get_taxonomy


def build_recommendation(profile: dict, target_career_id: str | None = None, top_n: int = 5) -> dict:
    tax, engine = get_taxonomy(), get_engine()
    owned, unknown = tax.parse_skill_list(profile.get("skills", []))
    ranked = engine.rank_careers(owned)
    target = target_career_id or ranked[0].career_id
    if target not in tax.careers:
        raise KeyError(f"Unknown career id: {target}")
    gaps = engine.skill_gaps(target, owned)
    top_gaps = [g.skill_id for g in gaps[:6]]
    return {
        "profile": {**{k: v for k, v in profile.items() if k != "skills"}, "skills": owned,
                    "unrecognised_skills": unknown},
        "careers": [m.to_dict() for m in ranked[:top_n]],
        "target_career": {"career_id": target, "career_name": tax.careers[target].name},
        "skill_gaps": [g.__dict__ for g in gaps],
        "learning_resources": [r.to_dict() for r in rank_resources(top_gaps)],
        "portfolio_projects": [
            {"project_id": p.project_id, "title": p.title, "skills": list(p.skills), "difficulty": p.difficulty,
             "estimated_hours": p.hours, "description": p.description, "score": s}
            for p, s in rank_projects(target, top_gaps)
        ],
        "model_version": config.MODEL_VERSION,
        "disclaimer": "Scores are compatibility indicators, not probabilities of employment.",
    }
