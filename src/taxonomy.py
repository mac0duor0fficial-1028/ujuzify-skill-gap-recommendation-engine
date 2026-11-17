"""Governed skill and career taxonomy loaded from versioned CSV reference data."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from src import config

_SPACE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """Lowercase, unify separators and collapse whitespace (never delete it).

    Technical symbols that carry meaning in skill names (+, #, .) are retained.
    """
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"[-/_]", " ", text)
    text = re.sub(r"[^a-z0-9+#.\s]", " ", text)
    text = re.sub(r"(?<=\s)\.+|\.+(?=\s|$)", " ", text)  # sentence dots, keep node.js
    return _SPACE.sub(" ", text).strip()


@dataclass(frozen=True)
class Skill:
    skill_id: str
    canonical_name: str
    display_name: str
    category: str
    aliases: tuple[str, ...]
    related: tuple[str, ...]


@dataclass(frozen=True)
class Career:
    career_id: str
    name: str
    family: str
    description: str
    title_keywords: tuple[str, ...]
    skills: dict[str, float] = field(default_factory=dict)


CATEGORY_LABELS = {
    "programming": "Programming",
    "databases": "Databases & Modeling",
    "analytics": "Analytics & Visualization",
    "ai_ml": "Machine Learning & AI",
    "data_engineering": "Data Engineering",
    "cloud": "Cloud & Infrastructure",
    "devops": "DevOps & Delivery",
    "software_engineering": "Software Engineering",
    "security": "Cybersecurity",
    "professional": "Professional Practice",
}


class Taxonomy:
    def __init__(self, skills: list[Skill], careers: list[Career]):
        self.skills = {s.canonical_name: s for s in skills}
        self.careers = {c.career_id: c for c in careers}
        self._alias: dict[str, str] = {}
        for s in skills:
            for term in self.terms_for(s):
                self._alias.setdefault(term, s.canonical_name)

    @staticmethod
    def terms_for(skill: Skill) -> list[str]:
        terms: list[str] = []
        for t in (skill.canonical_name, skill.display_name):
            n = normalize_text(t)
            if n and not (len(n) <= 2 and n.isalpha()) and n not in terms:  # 'go', 'r' are too ambiguous alone
                terms.append(n)
        for alias in skill.aliases:
            n = normalize_text(alias)
            if n and n not in terms:
                terms.append(n)
        return terms

    # ------------------------------------------------------------------ lookup
    def resolve(self, text: str | None) -> str | None:
        """Map a free-text skill name to its canonical id, or None if unknown."""
        n = normalize_text(text)
        if not n:
            return None
        if n in self._alias:
            return self._alias[n]
        if n in ("go", "r"):
            return None
        return None

    def display(self, canonical: str) -> str:
        s = self.skills.get(canonical)
        return s.display_name if s else canonical

    def category_of(self, canonical: str) -> str | None:
        s = self.skills.get(canonical)
        return s.category if s else None

    def related(self, canonical: str) -> tuple[str, ...]:
        s = self.skills.get(canonical)
        return s.related if s else ()

    def parse_skill_list(self, raw: str | list[str] | None) -> tuple[list[str], list[str]]:
        """Return (canonical skills, unrecognised free-text skills), de-duplicated in order."""
        if raw is None:
            return [], []
        items = re.split(r"[|,;\n]+", raw) if isinstance(raw, str) else list(raw)
        known: list[str] = []
        unknown: list[str] = []
        for item in items:
            item = str(item).strip()
            if not item:
                continue
            canon = self.resolve(item)
            if canon:
                if canon not in known:
                    known.append(canon)
            elif item.lower() not in {u.lower() for u in unknown}:
                unknown.append(item)
        return known, unknown

    def career_by_name(self, name: str) -> Career | None:
        n = normalize_text(name)
        for c in self.careers.values():
            if normalize_text(c.name) == n:
                return c
        return None

    def match_career_title(self, text: str | None) -> Career | None:
        """Resolve a job title / short role text to a career using title keywords.

        The longest matching keyword wins so 'machine learning engineer' is never
        mistaken for a plain 'software engineer'.
        """
        n = normalize_text(text)
        if not n:
            return None
        best: tuple[int, Career] | None = None
        for c in self.careers.values():
            candidates = [normalize_text(c.name), *[normalize_text(k) for k in c.title_keywords]]
            for kw in candidates:
                if kw and re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", n):
                    if best is None or len(kw) > best[0]:
                        best = (len(kw), c)
        return best[1] if best else None


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_taxonomy(
    skills_csv: Path | None = None,
    careers_csv: Path | None = None,
    profiles_csv: Path | None = None,
) -> Taxonomy:
    skills_csv = skills_csv or config.SKILL_TAXONOMY_CSV
    careers_csv = careers_csv or config.CAREER_TAXONOMY_CSV
    profiles_csv = profiles_csv or config.CAREER_PROFILES_CSV
    required = {"skill_id", "canonical_name", "display_name", "category", "aliases", "related_skills"}
    rows = _read(skills_csv)
    if not rows or not required.issubset(rows[0].keys()):
        raise ValueError(f"{skills_csv} is missing required columns: {sorted(required)}")
    skills = [
        Skill(
            r["skill_id"], r["canonical_name"], r["display_name"], r["category"],
            tuple(a for a in r["aliases"].split("|") if a),
            tuple(a for a in r["related_skills"].split("|") if a),
        )
        for r in rows
    ]
    weights: dict[str, dict[str, float]] = {}
    for r in _read(profiles_csv):
        weights.setdefault(r["career_id"], {})[r["skill_id"]] = float(r["importance"])
    known = {s.canonical_name for s in skills}
    careers = []
    for r in _read(careers_csv):
        prof = weights.get(r["career_id"], {})
        bad = [k for k in prof if k not in known]
        if bad:
            raise ValueError(f"career {r['career_id']} references unknown skills: {bad}")
        careers.append(
            Career(r["career_id"], r["career_name"], r["career_family"], r["description"],
                   tuple(k for k in r["title_keywords"].split("|") if k), prof)
        )
    return Taxonomy(skills, careers)


@lru_cache(maxsize=1)
def get_taxonomy() -> Taxonomy:
    return load_taxonomy()
