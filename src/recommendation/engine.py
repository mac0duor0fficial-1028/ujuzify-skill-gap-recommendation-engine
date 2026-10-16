"""Career recommendation and skill-gap engine (Phase 5).

Models (docs/evaluation_strategy.md section 4, increasing sophistication):

* ``popularity_v0``        baseline 1: the same ranking for everyone, by posting volume.
* ``keyword_overlap_v0``   baseline 2: how many of the profile's skills the career mentions.
* ``tfidf_cosine_v0``      baseline 3: TF-IDF cosine between profile and career skill vector.
* ``weighted_coverage_v1`` the engine: the share of a career's total skill importance that
                           the profile already covers (see ``score_weighted_coverage``).

Scores are *compatibility scores*, never probabilities of being hired (explainability
rule 1). Every recommendation lists matched and missing skills with the career's
importance and observed frequency (rule 2); frequencies are sample statistics (rule 3).
The engine has no built-in career or skill catalog: it reads career profiles produced
from data (profiles.py) and prerequisite pairs from reference data (rule 7).

Standard library only.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

from src.nlp.skill_dictionary import SkillDictionary
from src.recommendation.profiles import CareerProfile

MODEL_VERSION = "weighted_coverage_v1"
BASELINE_MODELS = ("popularity_v0", "keyword_overlap_v0", "tfidf_cosine_v0")

DISCLAIMER = ("Scores show how closely your current skills match each career's typical "
              "requirements in our sample of job postings. They do not predict whether you "
              "will be hired.")
DATA_LIMITS = ("Demand figures are sample statistics from the acquired job corpus, not a "
               "definitive ranking of the Kenyan labour market.")

ROOT = Path(__file__).resolve().parents[2]
PREREQUISITES_CSV = ROOT / "data" / "reference" / "skill_prerequisites.csv"


# ------------------------------------------------------------------ scoring models
Scores = dict  # career_id -> float


def score_popularity(skills: frozenset, profiles: dict) -> Scores:
    total = sum(p.postings for p in profiles.values()) or 1
    return {cid: p.postings / total for cid, p in profiles.items()}


def score_keyword_overlap(skills: frozenset, profiles: dict) -> Scores:
    if not skills:
        return {cid: 0.0 for cid in profiles}
    return {cid: len(skills & set(p.skills)) / len(skills) for cid, p in profiles.items()}


def score_tfidf_cosine(skills: frozenset, profiles: dict) -> Scores:
    n_careers = len(profiles)
    df: dict[str, int] = {}
    for p in profiles.values():
        for sid in p.skills:
            df[sid] = df.get(sid, 0) + 1
    idf = {sid: math.log((n_careers + 1) / (d + 1)) + 1.0 for sid, d in df.items()}
    query = {sid: idf[sid] for sid in skills if sid in idf}
    q_norm = math.sqrt(sum(v * v for v in query.values()))
    out: Scores = {}
    for cid, p in profiles.items():
        vec = {sid: st.frequency * idf[sid] for sid, st in p.skills.items()}
        v_norm = math.sqrt(sum(v * v for v in vec.values()))
        dot = sum(w * vec.get(sid, 0.0) for sid, w in query.items())
        out[cid] = dot / (q_norm * v_norm) if q_norm and v_norm else 0.0
    return out


def score_weighted_coverage(skills: frozenset, profiles: dict) -> Scores:
    """Compatibility = sum of career importance over the skills you have, divided by the sum
    over all the career's skills: "how much of what this career typically asks for (weighted
    by importance) do you already cover". Range 0 to 1; 1 means every skill the sampled
    postings ask for. Skills you have that the career does not ask for do not count for or
    against it."""
    out: Scores = {}
    for cid, p in profiles.items():
        total = sum(st.importance for st in p.skills.values())
        have = sum(st.importance for sid, st in p.skills.items() if sid in skills)
        out[cid] = have / total if total else 0.0
    return out


MODELS = {
    "popularity_v0": score_popularity,
    "keyword_overlap_v0": score_keyword_overlap,
    "tfidf_cosine_v0": score_tfidf_cosine,
    MODEL_VERSION: score_weighted_coverage,
}


def rank_careers(scores: Scores, profiles: dict) -> list[str]:
    """Deterministic ranking: score, then posting volume, then career_id."""
    return sorted(scores, key=lambda c: (-round(scores[c], 12), -profiles[c].postings, c))


# ------------------------------------------------------------------ evidence
@dataclass(frozen=True)
class SkillEvidence:
    skill_id: str
    canonical_name: str
    display_name: str
    category: str
    frequency: float
    importance: float
    postings_with_skill: int
    postings: int
    reason: str


@dataclass(frozen=True)
class CareerMatch:
    rank: int
    career_id: str
    career_name: str
    score: float
    postings: int
    low_sample: bool
    matched: list
    missing: list
    model_version: str
    tied_with: int = 0      # other careers with exactly the same score (rank order among them is arbitrary)


def _evidence(sid: str, prof: CareerProfile, dictionary: SkillDictionary, have: bool) -> SkillEvidence:
    st, sk = prof.skills[sid], dictionary.skills[sid]
    share = f"appears in {st.frequency:.0%} of {prof.postings} sampled {prof.career_name} postings"
    reason = f"{'you have it; ' if have else ''}{share} (importance {st.importance:.2f})"
    return SkillEvidence(sid, sk.canonical_name, sk.display_name, sk.category, st.frequency,
                         st.importance, st.count, prof.postings, reason)


def _by_weight(profile: CareerProfile, ids) -> list[str]:
    return sorted(ids, key=lambda s: (-profile.skills[s].importance, -profile.skills[s].frequency, s))


def recommend(skill_ids, profiles: dict, dictionary: SkillDictionary,
              model: str = MODEL_VERSION, top: int | None = None) -> list[CareerMatch]:
    """Rank careers for a profile and attach matched/missing skill evidence to each."""
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; choose from {sorted(MODELS)}")
    skills = frozenset(skill_ids)
    scores = MODELS[model](skills, profiles)
    out = []
    rounded = [round(v, 9) for v in scores.values()]
    for rank, cid in enumerate(rank_careers(scores, profiles), 1):
        prof = profiles[cid]
        matched = _by_weight(prof, [s for s in prof.skills if s in skills])
        missing = _by_weight(prof, [s for s in prof.skills if s not in skills])
        out.append(CareerMatch(
            rank, cid, prof.career_name, scores[cid], prof.postings, prof.low_sample,
            [_evidence(s, prof, dictionary, True) for s in matched],
            [_evidence(s, prof, dictionary, False) for s in missing], model,
            rounded.count(round(scores[cid], 9)) - 1))
    return out[:top] if top else out


# ------------------------------------------------------------------ skill-gap analysis
@dataclass(frozen=True)
class GapItem:
    priority: int
    skill_id: str
    canonical_name: str
    display_name: str
    importance: float
    frequency: float
    postings_with_skill: int
    postings: int
    requires: list = field(default_factory=list)   # [(display_name, you_have_it)]
    reason: str = ""
    resource: str | None = None                    # filled in by Phase 6 (learning resources)


@dataclass(frozen=True)
class GapReport:
    career_id: str
    career_name: str
    postings: int
    low_sample: bool
    have: list
    missing: list


def load_prerequisites(path: Path | str | None, dictionary: SkillDictionary) -> dict[str, list[str]]:
    """Read skill -> [prerequisite skills] from reference data; reject unknown names and cycles."""
    p = Path(path) if path else PREREQUISITES_CSV
    pre: dict[str, list[str]] = {}
    with p.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            a, b = dictionary.by_canonical.get(r["skill"]), dictionary.by_canonical.get(r["requires"])
            if a is None or b is None:
                raise ValueError(f"prerequisite row {r['skill']!r} -> {r['requires']!r}: unknown skill name")
            if b.skill_id not in pre.setdefault(a.skill_id, []):
                pre[a.skill_id].append(b.skill_id)
    state: dict[str, int] = {}

    def visit(s: str) -> None:
        if state.get(s) == 1:
            raise ValueError(f"prerequisite cycle through {dictionary.skills[s].canonical_name}")
        if state.get(s) == 2:
            return
        state[s] = 1
        for q in pre.get(s, []):
            visit(q)
        state[s] = 2

    for s in list(pre):
        visit(s)
    return pre


def gap_analysis(skill_ids, profile: CareerProfile, dictionary: SkillDictionary,
                 prerequisites: dict[str, list[str]] | None = None) -> GapReport:
    """Missing skills for one career, ordered by importance, then by observed frequency, with
    prerequisites placed before the skills that build on them (when both are missing)."""
    prerequisites = prerequisites or {}
    skills = frozenset(skill_ids)
    missing_ids = set(s for s in profile.skills if s not in skills)
    base = _by_weight(profile, missing_ids)

    order: list[str] = []
    seen: set[str] = set()

    def emit(s: str) -> None:
        if s in seen:
            return
        seen.add(s)
        for q in prerequisites.get(s, []):
            if q in missing_ids:
                emit(q)
        order.append(s)

    for s in base:
        emit(s)

    items = []
    for i, s in enumerate(order, 1):
        st = profile.skills[s]
        reqs = [(dictionary.skills[q].display_name, q in skills) for q in prerequisites.get(s, [])]
        reason = (f"appears in {st.frequency:.0%} of {profile.postings} sampled "
                  f"{profile.career_name} postings (importance {st.importance:.2f})")
        if reqs:
            parts = [f"{n} ({'you have it' if ok else 'missing, learn first'})" for n, ok in reqs]
            reason += "; builds on " + ", ".join(parts)
        items.append(GapItem(i, s, dictionary.skills[s].canonical_name, dictionary.skills[s].display_name,
                             st.importance, st.frequency,
                             st.count, profile.postings, reqs, reason))
    have = [_evidence(s, profile, dictionary, True) for s in _by_weight(profile, [s for s in profile.skills if s in skills])]
    return GapReport(profile.career_id, profile.career_name, profile.postings, profile.low_sample, have, items)


# ------------------------------------------------------------------ profile input
def resolve_skills(raw_skills: list[str], dictionary: SkillDictionary) -> tuple[frozenset, list[str]]:
    """Map free-text skill entries to skill ids. Unrecognised entries are returned, not dropped silently."""
    ids, unknown = set(), []
    for raw in raw_skills:
        sk = dictionary.lookup(raw)
        if sk is None:
            unknown.append(raw)
        else:
            ids.add(sk.skill_id)
    return frozenset(ids), unknown
