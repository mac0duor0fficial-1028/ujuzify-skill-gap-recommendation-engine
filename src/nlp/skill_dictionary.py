"""Skill dictionary and career taxonomy access (Phase 2).

Loads the Phase 1 governed vocabularies (``data/external/skill_taxonomy.csv``,
``career_taxonomy.csv``) plus ``career_title_aliases.csv`` (raw job title -> career).

``SkillDictionary.scan`` is a deliberately simple, phrase-based lookup. It exists so
Phase 2 can report dictionary coverage of ingested postings. It is NOT the Phase 4
extractor: it has no context handling, no confidence, and its precision has not been
measured against a gold set (see docs/evaluation_strategy.md section 2).

Standard library only.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS_CSV = ROOT / "data" / "external" / "skill_taxonomy.csv"
CAREERS_CSV = ROOT / "data" / "external" / "career_taxonomy.csv"
TITLE_ALIASES_CSV = ROOT / "data" / "external" / "career_title_aliases.csv"

# Bare tokens that are common English words / single letters. Phase 1 governance:
# they are matched only through unambiguous aliases ('r programming', 'golang').
# Phase 4 owns context-sensitive matching of these.
AMBIGUOUS_BARE_FORMS = frozenset({"r", "go"})

_SCAN_PUNCT_RE = re.compile(r"[,;:()\[\]{}\"'!?|*\u2022\u2013\u2014\n\r]")


def normalize(text: str) -> str:
    """Identical to scripts/validate_taxonomies.normalize (parity is tested)."""
    t = text.lower().replace("&", " and ")
    t = re.sub(r"[-/_]", " ", t)
    t = re.sub(r"[^a-z0-9+#.\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str) -> list[str]:
    """Free-text tokenization: punctuation separates words; inner dots survive (node.js)."""
    t = _SCAN_PUNCT_RE.sub(" ", text)
    toks = [tok.strip(".") for tok in normalize(t).split()]
    return [tok for tok in toks if tok]


@dataclass(frozen=True)
class Skill:
    skill_id: str
    canonical_name: str
    display_name: str
    category: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class SkillMatch:
    skill_id: str
    canonical_name: str
    surface: str  # normalized text that matched


class SkillDictionary:
    def __init__(self, skills: list[Skill]):
        self.skills: dict[str, Skill] = {s.skill_id: s for s in skills}
        self.by_canonical: dict[str, Skill] = {s.canonical_name: s for s in skills}
        self._forms: dict[str, str] = {}  # normalized form -> skill_id (all forms)
        self._scan_index: dict[tuple[str, ...], str] = {}
        for s in skills:
            forms = {normalize(s.canonical_name), normalize(s.display_name)}
            forms.update(normalize(a) for a in s.aliases)
            forms.discard("")
            for form in forms:
                owner = self._forms.setdefault(form, s.skill_id)
                if owner != s.skill_id:
                    raise ValueError(f"Alias collision: '{form}' -> {owner} and {s.skill_id}")
                if form not in AMBIGUOUS_BARE_FORMS:
                    self._scan_index[tuple(form.split())] = s.skill_id
        self._max_len = max((len(k) for k in self._scan_index), default=1)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "SkillDictionary":
        p = Path(path) if path else SKILLS_CSV
        with p.open(newline="", encoding="utf-8") as fh:
            skills = [
                Skill(
                    skill_id=r["skill_id"], canonical_name=r["canonical_name"],
                    display_name=r["display_name"], category=r["category"],
                    aliases=tuple(a.strip() for a in r["aliases"].split("|") if a.strip()),
                )
                for r in csv.DictReader(fh)
            ]
        return cls(skills)

    def lookup(self, term: str) -> Skill | None:
        """Resolve one explicit skill term (e.g. a profile entry) to a Skill.

        Unlike scan(), this resolves bare 'r' and 'go': a user who typed exactly that
        as a skill is not ambiguous prose.
        """
        sid = self._forms.get(normalize(term))
        return self.skills[sid] if sid else None

    def match_tokens(self, toks: list[str]) -> list[tuple[int, int, str]]:
        """All longest-phrase matches over normalized tokens as (start, end_exclusive, skill_id).

        Matches do not overlap and a skill may appear more than once. Bare 'r' and 'go'
        are never returned (see AMBIGUOUS_BARE_FORMS).
        """
        out: list[tuple[int, int, str]] = []
        i = 0
        while i < len(toks):
            for n in range(min(self._max_len, len(toks) - i), 0, -1):
                sid = self._scan_index.get(tuple(toks[i:i + n]))
                if sid:
                    out.append((i, i + n, sid))
                    i += n
                    break
            else:
                i += 1
        return out

    def scan(self, text: str) -> list[SkillMatch]:
        """Longest-phrase dictionary matches in free text; one match per skill, in order."""
        toks = _tokens(text)
        found: dict[str, SkillMatch] = {}
        for a, b, sid in self.match_tokens(toks):
            if sid not in found:
                found[sid] = SkillMatch(sid, self.skills[sid].canonical_name, " ".join(toks[a:b]))
        return list(found.values())

    def skill_ids_in(self, text: str) -> list[str]:
        return [m.skill_id for m in self.scan(text)]


@dataclass(frozen=True)
class Career:
    career_id: str
    career_name: str
    career_family: str
    description: str


class CareerTaxonomy:
    def __init__(self, careers: list[Career], title_aliases: list[tuple[str, str]]):
        self.careers: dict[str, Career] = {c.career_id: c for c in careers}
        self._alias_index: dict[tuple[str, ...], str] = {}
        for career_id, alias in title_aliases:
            if career_id not in self.careers:
                raise ValueError(f"title alias '{alias}' references unknown career {career_id}")
            key = tuple(normalize(alias).split())
            owner = self._alias_index.setdefault(key, career_id)
            if owner != career_id:
                raise ValueError(f"Title alias collision: '{alias}' -> {owner} and {career_id}")
        self._max_len = max((len(k) for k in self._alias_index), default=1)

    @classmethod
    def load(cls, careers_path: Path | str | None = None,
             aliases_path: Path | str | None = None) -> "CareerTaxonomy":
        cp = Path(careers_path) if careers_path else CAREERS_CSV
        ap = Path(aliases_path) if aliases_path else TITLE_ALIASES_CSV
        with cp.open(newline="", encoding="utf-8") as fh:
            careers = [Career(r["career_id"], r["career_name"], r["career_family"], r["description"])
                       for r in csv.DictReader(fh)]
        with ap.open(newline="", encoding="utf-8") as fh:
            aliases = [(r["career_id"], r["title_alias"]) for r in csv.DictReader(fh)]
        return cls(careers, aliases)

    def match_title_with_alias(self, title: str) -> tuple[Career, str] | None:
        """Like match_title, but also returns the (normalized) alias that matched.

        The longest alias phrase found in the title wins. If two different careers tie
        on length the title is ambiguous and None is returned (no guessing).
        """
        toks = _tokens(title)
        best_len = 0
        best: dict[str, str] = {}  # career_id -> matched alias
        for i in range(len(toks)):
            for n in range(min(self._max_len, len(toks) - i), 0, -1):
                key = tuple(toks[i:i + n])
                cid = self._alias_index.get(key)
                if cid:
                    if n > best_len:
                        best_len, best = n, {cid: " ".join(key)}
                    elif n == best_len:
                        best.setdefault(cid, " ".join(key))
                    break
        if len(best) == 1:
            cid, alias = next(iter(best.items()))
            return self.careers[cid], alias
        return None

    def match_title(self, title: str) -> Career | None:
        """Map a raw job title to one of the nine careers, or None."""
        hit = self.match_title_with_alias(title)
        return hit[0] if hit else None
