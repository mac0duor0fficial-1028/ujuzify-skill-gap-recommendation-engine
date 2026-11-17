"""Explainable career ranking and skill-gap prioritisation (project report, Section 3.8.2).

Weighted Skill Coverage = sum(weight of owned required skills) / sum(all required weights)
Market Adjustment       = min(1, mean(demand rate of required skills) * 5)
Match Score             = 0.85 * coverage + 0.15 * market_adjustment
Gap Priority            = importance * (0.75 + 0.50 * demand_rate)

Scores are compatibility indicators, never probabilities of employment. When no observed
market-demand data exist the engine runs on coverage alone and does not invent demand.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from src import config
from src.taxonomy import Career, Taxonomy, get_taxonomy


def load_demand(path: Path | None = None) -> dict[str, float]:
    """Load observed demand rates (skill -> share of sampled jobs mentioning it)."""
    path = path or config.SKILL_DEMAND_CSV
    if not Path(path).exists():
        return {}
    out: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[r["skill_id"]] = float(r["demand_rate"])
            except (KeyError, ValueError):
                continue
    return out


@dataclass
class CareerMatch:
    career_id: str
    career_name: str
    match_score: float
    skill_coverage: float
    market_demand: float | None
    matched_skills: list[str]
    missing_skills: list[str]
    required_count: int
    explanation: str
    confidence: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SkillGap:
    skill_id: str
    display_name: str
    importance: float
    demand_rate: float | None
    priority: float
    reason: str


@dataclass
class Engine:
    taxonomy: Taxonomy = field(default_factory=get_taxonomy)
    demand: dict[str, float] = field(default_factory=dict)

    # --------------------------------------------------------------- primitives
    def coverage(self, owned: set[str], required: dict[str, float], partial: set[str] | None = None) -> float:
        total = sum(required.values())
        if total <= 0:
            return 0.0
        partial = partial or set()
        got = sum(w for s, w in required.items() if s in owned)
        got += sum(w * config.PARTIAL_CREDIT for s, w in required.items() if s in partial and s not in owned)
        return got / total

    def market_adjustment(self, required: dict[str, float]) -> float | None:
        if not self.demand or not required:
            return None
        rates = [self.demand.get(s, 0.0) for s in required]
        return min(1.0, (sum(rates) / len(rates)) * config.MARKET_SCALE)

    def match_score(self, coverage: float, market: float | None) -> float:
        if market is None:
            return coverage
        return config.COVERAGE_WEIGHT * coverage + config.MARKET_WEIGHT * market

    def gap_priority(self, importance: float, demand_rate: float | None) -> float:
        return importance * (0.75 + 0.50 * (demand_rate or 0.0))

    # ------------------------------------------------------------------- public
    def rank_careers(self, owned_skills: list[str] | set[str]) -> list[CareerMatch]:
        owned = set(owned_skills)
        out: list[CareerMatch] = []
        for c in self.taxonomy.careers.values():
            cov = self.coverage(owned, c.skills)
            mkt = self.market_adjustment(c.skills)
            score = self.match_score(cov, mkt)
            matched = [s for s in c.skills if s in owned]
            missing = [s for s in c.skills if s not in owned]
            out.append(
                CareerMatch(
                    c.career_id, c.name, round(score, 4), round(cov, 4),
                    None if not self.demand else round(sum(self.demand.get(s, 0) for s in c.skills) / len(c.skills), 4),
                    matched, missing, len(c.skills), self._explain(c, matched, missing, cov),
                    confidence="strong" if score >= 0.7 else "moderate" if score >= 0.4 else "weak",
                )
            )
        out.sort(key=lambda m: (-m.match_score, m.career_name))
        return out

    def _explain(self, c: Career, matched: list[str], missing: list[str], cov: float) -> str:
        top_m = sorted(matched, key=lambda s: -c.skills[s])[:3]
        top_x = sorted(missing, key=lambda s: -c.skills[s])[:2]
        names = lambda xs: ", ".join(self.taxonomy.display(x) for x in xs)  # noqa: E731
        parts = [f"{round(cov * 100)}% of the weighted skills for {c.name} are covered."]
        if top_m:
            parts.append(f"Strongest matches: {names(top_m)}.")
        if top_x:
            parts.append(f"Biggest gaps: {names(top_x)}.")
        return " ".join(parts)

    def skill_gaps(self, career_id: str, owned_skills: list[str] | set[str]) -> list[SkillGap]:
        career = self.taxonomy.careers.get(career_id)
        if career is None:
            raise KeyError(f"Unknown career id: {career_id}")
        owned = set(owned_skills)
        gaps = []
        for s, w in career.skills.items():
            if s in owned:
                continue
            d = self.demand.get(s) if self.demand else None
            gaps.append(
                SkillGap(
                    s, self.taxonomy.display(s), w, d, round(self.gap_priority(w, d), 4),
                    f"{self.taxonomy.display(s)} carries importance {w:.2f} for {career.name}"
                    + (f" and appears in {d * 100:.0f}% of sampled postings." if d is not None else "."),
                )
            )
        gaps.sort(key=lambda g: (-g.priority, g.skill_id))
        return gaps


_engine: Engine | None = None


def get_engine(reload: bool = False) -> Engine:
    global _engine
    if _engine is None or reload:
        _engine = Engine(get_taxonomy(), load_demand())
    return _engine
