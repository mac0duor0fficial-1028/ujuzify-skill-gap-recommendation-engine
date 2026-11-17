"""Explainable, taxonomy-governed skill extraction.

Pipeline: normalise text -> phrase match (spaCy PhraseMatcher when installed, otherwise an
equivalent compiled-regex matcher) -> alias->canonical mapping -> overlap resolution ->
one evidence record per (document, skill).

The extractor never invents skills: it only recognises terms approved in the taxonomy.
Confidence is a deterministic ranking signal, NOT a calibrated probability.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from src.taxonomy import Taxonomy, get_taxonomy, normalize_text

CONTEXT_CHARS = 60
EXACT_CONFIDENCE = 0.95
ALIAS_CONFIDENCE = 0.85


@dataclass(frozen=True)
class Mention:
    skill_id: str
    canonical_name: str
    category: str
    matched_text: str
    context: str
    extraction_method: str
    confidence: float
    start: int
    end: int

    def to_dict(self) -> dict:
        return asdict(self)


class SkillExtractor:
    def __init__(self, taxonomy: Taxonomy | None = None, backend: str = "auto"):
        self.taxonomy = taxonomy or get_taxonomy()
        # term -> (canonical, is_exact)
        self._terms: dict[str, tuple[str, bool]] = {}
        for skill in self.taxonomy.skills.values():
            exact = {normalize_text(skill.canonical_name), normalize_text(skill.display_name)}
            for term in self.taxonomy.terms_for(skill):
                self._terms.setdefault(term, (skill.canonical_name, term in exact))
        self.backend = self._select_backend(backend)
        self._regex = None
        self._matcher = None
        self._nlp = None
        if self.backend == "spacy":
            self._init_spacy()
        else:
            self._init_regex()

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _select_backend(requested: str) -> str:
        if requested == "regex":
            return "regex"
        try:
            import spacy  # noqa: F401
            return "spacy"
        except Exception:  # pragma: no cover - depends on environment
            if requested == "spacy":
                raise
            return "regex"

    def _init_regex(self) -> None:
        terms = sorted(self._terms, key=len, reverse=True)
        body = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in terms)
        self._regex = re.compile(rf"(?<![a-z0-9+#.])(?:{body})(?![a-z0-9+#])")

    def _init_spacy(self) -> None:  # pragma: no cover - optional dependency
        import spacy
        from spacy.matcher import PhraseMatcher

        self._nlp = spacy.blank("en")
        self._matcher = PhraseMatcher(self._nlp.vocab, attr="LOWER")
        for term in self._terms:
            self._matcher.add(term, [self._nlp.make_doc(term)])

    # ---------------------------------------------------------------- matching
    def _candidates(self, text: str) -> list[tuple[int, int, str]]:
        if self.backend == "spacy":  # pragma: no cover
            doc = self._nlp.make_doc(text)
            out = []
            for match_id, s, e in self._matcher(doc):
                span = doc[s:e]
                term = self._nlp.vocab.strings[match_id]
                out.append((span.start_char, span.end_char, term))
            return out
        return [(m.start(), m.end(), re.sub(r"\s+", " ", m.group(0))) for m in self._regex.finditer(text)]

    def extract(self, raw_text: str | None) -> list[Mention]:
        text = normalize_text(raw_text)
        if not text:
            return []
        cands = []
        for start, end, term in self._candidates(text):
            canon, exact = self._terms[term]
            cands.append((start, end, canon, exact))
        # longest, most confident first; accept only non-overlapping spans
        cands.sort(key=lambda c: (-(c[1] - c[0]), -int(c[3]), c[0]))
        accepted: list[tuple[int, int, str, bool]] = []
        for c in cands:
            if all(c[1] <= a[0] or c[0] >= a[1] for a in accepted):
                accepted.append(c)
        accepted.sort(key=lambda c: c[0])
        seen: set[str] = set()
        mentions: list[Mention] = []
        for start, end, canon, exact in accepted:
            if canon in seen:  # one relationship per (document, skill)
                continue
            seen.add(canon)
            skill = self.taxonomy.skills[canon]
            ctx = text[max(0, start - CONTEXT_CHARS): end + CONTEXT_CHARS]
            mentions.append(
                Mention(
                    skill_id=canon, canonical_name=canon, category=skill.category,
                    matched_text=text[start:end], context=ctx,
                    extraction_method=f"phrase_match:{self.backend}",
                    confidence=EXACT_CONFIDENCE if exact else ALIAS_CONFIDENCE,
                    start=start, end=end,
                )
            )
        return mentions

    def skills(self, raw_text: str | None) -> list[str]:
        return [m.canonical_name for m in self.extract(raw_text)]


_default: SkillExtractor | None = None


def get_extractor() -> SkillExtractor:
    global _default
    if _default is None:
        _default = SkillExtractor()
    return _default
