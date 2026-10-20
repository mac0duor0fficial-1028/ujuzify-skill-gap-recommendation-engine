"""Learning-resource catalog and gap-to-resource matching (Phase 6).

Resources live in editable reference data (``data/reference/learning_resources.csv``), not in
code (explainability rule 7). Every skill gap should lead to something actionable (rule 6):
``attach_resources`` gives each gap item a primary resource and up to two alternatives, or says
plainly that none is listed.

Selection is deliberately simple and explainable: among resources for the skill that pass the
cost filter, the authored ``priority`` decides (lower first), then easier difficulty, then id.
It does not yet use proficiency, time or budget beyond a free-only switch; the catalog is a
hand-authored starter list.

Standard library only.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from src.nlp.skill_dictionary import SkillDictionary

ROOT = Path(__file__).resolve().parents[2]
RESOURCES_CSV = ROOT / "data" / "reference" / "learning_resources.csv"
COLUMNS = ["resource_id", "skill", "title", "provider", "url", "difficulty", "format", "cost", "priority"]
DIFFICULTIES = ("beginner", "intermediate", "advanced")
FORMATS = ("course", "tutorial", "documentation", "interactive", "video", "book")
COSTS = ("free", "freemium", "paid")   # 'free' = free to access; 'freemium' = free tier or audit only
MAX_ALTERNATIVES = 2


@dataclass(frozen=True)
class Resource:
    resource_id: str
    skill_id: str
    canonical_name: str
    title: str
    provider: str
    url: str
    difficulty: str
    format: str
    cost: str
    priority: int


def resource_dict(r: Resource) -> dict:
    return {"resource_id": r.resource_id, "title": r.title, "provider": r.provider, "url": r.url,
            "difficulty": r.difficulty, "format": r.format, "cost": r.cost}


class ResourceCatalog:
    def __init__(self, resources: list[Resource]):
        self.resources = resources
        self._by_skill: dict[str, list[Resource]] = {}
        for r in resources:
            self._by_skill.setdefault(r.skill_id, []).append(r)

    @classmethod
    def load(cls, path: Path | str | None = None, dictionary: SkillDictionary | None = None) -> "ResourceCatalog":
        dictionary = dictionary or SkillDictionary.load()
        p = Path(path) if path else RESOURCES_CSV
        with p.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != COLUMNS:
                raise ValueError(f"learning resources columns must be {COLUMNS}, found {reader.fieldnames}")
            rows = list(reader)
        out, seen_ids, seen_pairs = [], set(), set()
        for r in rows:
            rid = r["resource_id"]
            where = f"resource {rid or '<no id>'}"
            if not rid or rid in seen_ids:
                raise ValueError(f"{where}: missing or duplicate resource_id")
            seen_ids.add(rid)
            sk = dictionary.by_canonical.get(r["skill"])
            if sk is None:
                raise ValueError(f"{where}: unknown skill {r['skill']!r}")
            url = urlparse(r["url"])
            if url.scheme != "https" or not url.netloc or " " in r["url"]:
                raise ValueError(f"{where}: url must be an https URL, got {r['url']!r}")
            for field, allowed in (("difficulty", DIFFICULTIES), ("format", FORMATS), ("cost", COSTS)):
                if r[field] not in allowed:
                    raise ValueError(f"{where}: {field} {r[field]!r} not in {allowed}")
            if not r["title"].strip() or not r["provider"].strip():
                raise ValueError(f"{where}: title and provider are required")
            if not r["priority"].isdigit() or int(r["priority"]) < 1:
                raise ValueError(f"{where}: priority must be an integer >= 1")
            if (sk.skill_id, r["url"]) in seen_pairs:
                raise ValueError(f"{where}: duplicate (skill, url)")
            seen_pairs.add((sk.skill_id, r["url"]))
            out.append(Resource(rid, sk.skill_id, sk.canonical_name, r["title"], r["provider"], r["url"],
                                r["difficulty"], r["format"], r["cost"], int(r["priority"])))
        return cls(out)

    def options(self, skill_id: str, free_only: bool = False) -> list[Resource]:
        found = [r for r in self._by_skill.get(skill_id, []) if not free_only or r.cost == "free"]
        return sorted(found, key=lambda r: (r.priority, DIFFICULTIES.index(r.difficulty), r.resource_id))

    def coverage(self, weights: dict[str, float], free_only: bool = False) -> float:
        """Share of total weight (e.g. career skill importance) whose skill has >= 1 resource."""
        total = sum(weights.values())
        covered = sum(w for s, w in weights.items() if self.options(s, free_only))
        return covered / total if total else 0.0


def attach_resources(gap_items: list[dict], catalog: ResourceCatalog, free_only: bool = False) -> None:
    """Fill in ``resource``, ``alternatives`` and ``resource_note`` on gap item dicts, in place."""
    for item in gap_items:
        opts = catalog.options(item["skill_id"], free_only)
        item["resource"] = resource_dict(opts[0]) if opts else None
        item["alternatives"] = [resource_dict(r) for r in opts[1:1 + MAX_ALTERNATIVES]]
        if opts:
            item["resource_note"] = None
        elif free_only and catalog.options(item["skill_id"]):
            item["resource_note"] = "No free resource is listed for this skill (paid or freemium options exist)."
        else:
            item["resource_note"] = "No learning resource is listed for this skill yet."
