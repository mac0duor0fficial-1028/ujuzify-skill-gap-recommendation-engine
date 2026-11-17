"""Catalogue-driven learning-resource and portfolio-project recommendation."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache

from src import config


@dataclass(frozen=True)
class Resource:
    resource_id: str
    skill_id: str
    title: str
    provider: str
    url: str
    cost_tier: str
    level: str
    hours: float
    resource_type: str
    description: str

    def to_dict(self) -> dict:
        return {"resource_id": self.resource_id, "skill": self.skill_id, "title": self.title,
                "provider": self.provider, "url": self.url, "tier": self.cost_tier,
                "level": self.level, "estimated_hours": self.hours, "type": self.resource_type,
                "description": self.description}


@dataclass(frozen=True)
class Project:
    project_id: str
    title: str
    career_ids: tuple[str, ...]
    skills: tuple[str, ...]
    difficulty: int
    hours: float
    portfolio_value: int
    description: str


@lru_cache(maxsize=1)
def load_resources() -> tuple[Resource, ...]:
    with open(config.LEARNING_RESOURCES_CSV, newline="", encoding="utf-8") as f:
        return tuple(
            Resource(r["resource_id"], r["skill_id"], r["title"], r["provider"], r["url"], r["cost_tier"],
                     r["level"], float(r["estimated_hours"] or 0), r["resource_type"], r["description"])
            for r in csv.DictReader(f)
        )


@lru_cache(maxsize=1)
def load_projects() -> tuple[Project, ...]:
    with open(config.PROJECTS_CSV, newline="", encoding="utf-8") as f:
        return tuple(
            Project(r["project_id"], r["title"], tuple(r["career_ids"].split("|")), tuple(r["skill_focus"].split("|")),
                    int(r["difficulty"]), float(r["estimated_hours"]), int(r["portfolio_value"]), r["description"])
            for r in csv.DictReader(f)
        )


def resources_for_skill(skill_id: str, limit: int = 3, free_first: bool = True) -> list[Resource]:
    found = [r for r in load_resources() if r.skill_id == skill_id]
    level_rank = {"beginner": 0, "intermediate": 1, "advanced": 2}
    found.sort(key=lambda r: ((r.cost_tier != "free") if free_first else 0, level_rank.get(r.level, 1), r.hours))
    return found[:limit]


def estimate_weeks(skill_id: str, weekly_hours: float = 8.0) -> int:
    rs = resources_for_skill(skill_id, limit=2)
    hours = sum(r.hours for r in rs) if rs else 12.0
    return max(1, math.ceil(hours / max(weekly_hours, 1.0)))


def rank_resources(missing_skills: list[str], per_skill: int = 2) -> list[Resource]:
    out: list[Resource] = []
    for s in missing_skills:
        out.extend(resources_for_skill(s, per_skill))
    return out


def rank_projects(career_id: str | None, missing_skills: list[str], limit: int = 3) -> list[tuple[Project, float]]:
    miss = set(missing_skills)
    scored = []
    for p in load_projects():
        overlap = len(miss & set(p.skills)) / max(len(p.skills), 1)
        career = 1.0 if career_id and career_id in p.career_ids else 0.0
        score = 0.45 * career + 0.40 * overlap + 0.15 * (p.portfolio_value / 5)
        if score > 0.15:
            scored.append((p, round(score, 4)))
    scored.sort(key=lambda x: (-x[1], x[0].project_id))
    return scored[:limit]


def project_for_skill(skill_id: str, career_id: str | None = None) -> Project | None:
    cands = [p for p in load_projects() if skill_id in p.skills]
    if not cands:
        return None
    cands.sort(key=lambda p: (-(career_id in p.career_ids if career_id else 0), p.difficulty, -p.portfolio_value))
    return cands[0]
