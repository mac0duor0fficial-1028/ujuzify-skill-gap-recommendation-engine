"""Career skill profiles built from observed postings (Phase 5).

A career profile answers "what do sampled postings for this career ask for?":

* ``frequency``  = share of the career's postings that mention the skill;
* ``importance`` = mean over the career's postings of the posting-level importance
  (1.0 normally, 0.5 where the posting says the skill is "a plus"; 0 where absent).
  It is therefore always <= frequency and in [0, 1].

Postings are assigned to careers by the Phase 3 weak title labels; skills come from the
Phase 4 extractor. Both are imperfect, and with small corpora the shares are lumpy: every
profile carries ``postings`` and ``low_sample`` so callers can say so (explainability
rule 3). Standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MIN_POSTINGS = 30  # careers with fewer postings are flagged low_sample


@dataclass(frozen=True)
class Posting:
    job_id: str
    career_id: str
    skills: dict          # skill_id -> posting-level importance in (0, 1]
    synthetic: bool = False


@dataclass(frozen=True)
class SkillStat:
    count: int            # postings mentioning the skill
    frequency: float      # count / postings
    importance: float     # sum of posting importance / postings


@dataclass
class CareerProfile:
    career_id: str
    career_name: str
    postings: int
    synthetic_postings: int
    low_sample: bool
    skills: dict = field(default_factory=dict)   # skill_id -> SkillStat


class CorpusCounts:
    """Per-career counts from which profiles are derived; supports leave-one-out."""

    def __init__(self, postings: list[Posting], career_names: dict[str, str],
                 min_postings: int = DEFAULT_MIN_POSTINGS):
        self.career_names = career_names
        self.min_postings = min_postings
        self.n: dict[str, int] = {}
        self.syn: dict[str, int] = {}
        self.count: dict[str, dict[str, int]] = {}
        self.imp: dict[str, dict[str, float]] = {}
        for p in postings:
            self._add(p, +1)

    def _add(self, p: Posting, sign: int) -> None:
        c = p.career_id
        self.n[c] = self.n.get(c, 0) + sign
        self.syn[c] = self.syn.get(c, 0) + sign * int(p.synthetic)
        cc, ii = self.count.setdefault(c, {}), self.imp.setdefault(c, {})
        for sid, w in p.skills.items():
            cc[sid] = cc.get(sid, 0) + sign
            ii[sid] = ii.get(sid, 0.0) + sign * w

    def profile(self, career_id: str, exclude: Posting | None = None) -> CareerProfile | None:
        """Profile of one career, optionally without one posting. None if no postings remain."""
        n, syn = self.n.get(career_id, 0), self.syn.get(career_id, 0)
        cc, ii = self.count.get(career_id, {}), self.imp.get(career_id, {})
        if exclude is not None and exclude.career_id == career_id:
            n, syn = n - 1, syn - int(exclude.synthetic)
            cc, ii = dict(cc), dict(ii)
            for sid, w in exclude.skills.items():
                cc[sid] -= 1
                ii[sid] -= w
        if n <= 0:
            return None
        skills = {sid: SkillStat(c, c / n, round(ii[sid], 9) / n)
                  for sid, c in cc.items() if c > 0}
        return CareerProfile(career_id, self.career_names.get(career_id, career_id), n, syn,
                             n < self.min_postings, skills)

    def profiles(self, exclude: Posting | None = None) -> dict[str, CareerProfile]:
        out = {}
        for cid in sorted(self.n):
            prof = self.profile(cid, exclude)
            if prof is not None:
                out[cid] = prof
        return out


def postings_from_rows(staging_rows: list[dict], label_rows: list[dict],
                       job_skills_rows: list[dict]) -> list[Posting]:
    """Join staging, weak labels and extracted skills into Postings (labeled ones only)."""
    label_by_job = {l["job_id"]: l for l in label_rows}
    skills_by_job: dict[str, dict] = {}
    for js in job_skills_rows:
        skills_by_job.setdefault(js["job_id"], {})[js["skill_id"]] = float(js["importance"])
    out = []
    for r in sorted(staging_rows, key=lambda r: r["job_id"]):
        label = label_by_job.get(r["job_id"])
        if not label or not label["career_id"]:
            continue
        out.append(Posting(r["job_id"], label["career_id"], skills_by_job.get(r["job_id"], {}),
                           r["is_synthetic"] == "true"))
    return out


def profiles_from_career_skill_rows(rows: list[dict]) -> dict[str, CareerProfile]:
    """Rebuild profiles from career_skills.csv rows."""
    out: dict[str, CareerProfile] = {}
    for r in rows:
        prof = out.get(r["career_id"])
        if prof is None:
            prof = out[r["career_id"]] = CareerProfile(
                r["career_id"], r["career_name"], int(r["postings_in_career"]),
                int(r["synthetic_postings_in_career"]), r["low_sample"] == "true")
        prof.skills[r["skill_id"]] = SkillStat(
            int(r["postings_with_skill"]), float(r["frequency"]), float(r["importance"]))
    return out
