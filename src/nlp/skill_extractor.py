"""Rule-based skill extractor, version 1 (Phase 4).

Links job-posting text to the governed skill taxonomy. It never invents skills: every
output skill_id comes from ``data/external/skill_taxonomy.csv``.

How it works
------------
1. Candidate generation: the Phase 2 longest-phrase dictionary match
   (``SkillDictionary.match_tokens``), so aliases, casing and punctuation behave exactly
   as in the dictionary.
2. Context rules that *remove* candidates which are ordinary English words in a
   non-technical sense ("shipping containers", "excel at", "monitoring of stock levels",
   "spark innovation"). Rules apply only to single-word matches of known risky forms;
   multi-word aliases such as 'microsoft excel' are always accepted. A risky word that
   lacks a technical cue word is still accepted when it sits in a comma/and list next to an
   already accepted skill ("Docker, Azure, monitoring, Bash scripting").
3. Context rules that *add* the bare tokens 'R' and 'Go', which the dictionary refuses to
   match on their own. They are accepted only as exact-case 'R' / 'Go' and only when
   preceded by in/with/using/of/for, or sitting in a list next to another accepted skill,
   and never for 'R&D'.
4. Requirement level, a heuristic: a sentence with an explicit "nice to have / a plus /
   an advantage" cue gives ``preferred`` (importance 0.5); an explicit "must / required /
   essential" cue gives ``required``; otherwise ``unspecified``. Required and unspecified
   both score importance 1.0. If a sentence has both kinds of cue it is ``unspecified``.

What it is not
--------------
Not a statistical model, not negation-aware ("no Java needed" still yields java), and not
validated on real Kenyan postings. Its measured behaviour on the small development gold
set is reported by ``src/evaluation/skill_extraction_eval.py``; see docs/phase4_nlp.md
for the limits. Standard library only.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from src.nlp.skill_dictionary import SkillDictionary

EXTRACTOR_METHOD = "rule_extractor_v1"
IMPORTANCE_PREFERRED = 0.5
IMPORTANCE_DEFAULT = 1.0

# ------------------------------------------------------------------ tokenization with offsets
# Mirrors skill_dictionary._tokens (parity is tested) but keeps raw text and offsets.
_CHUNK_RE = re.compile(r"&|[^\s\-/_,;:()\[\]{}\"'!?|*\u2022\u2013\u2014&]+")
_DROP_RE = re.compile(r"[^a-z0-9+#.]")


@dataclass(frozen=True)
class Tok:
    norm: str
    raw: str
    start: int
    end: int


def tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    for m in _CHUNK_RE.finditer(text):
        raw = m.group()
        norm = "and" if raw == "&" else _DROP_RE.sub("", raw.lower()).strip(".")
        if norm:
            toks.append(Tok(norm, raw, m.start(), m.end()))
    return toks


# ------------------------------------------------------------------ context rules
_VERB_PREV = frozenset({"to", "who", "will", "can", "could", "must", "should", "would", "may"})


@dataclass(frozen=True)
class Rule:
    """Context rule for one risky single-word form. Reject if a reject_* condition holds,
    or (when require_cue is set) unless a cue word is within ``window`` tokens."""
    reject_prev: frozenset = frozenset()
    reject_next: frozenset = frozenset()
    initial_reject_next: frozenset = frozenset()  # only when the word starts a sentence
    require_cue: frozenset = frozenset()
    window: int = 4


WEAK_RULES: dict[str, Rule] = {
    "excel": Rule(reject_prev=_VERB_PREV,
                  reject_next=frozenset({"at", "as", "under", "when", "while"}),
                  initial_reject_next=frozenset({"in", "at", "with", "under", "as"})),
    "react": Rule(reject_prev=_VERB_PREV,
                  reject_next=frozenset({"to", "quickly", "fast", "swiftly", "promptly", "well",
                                         "positively", "calmly"})),
    "spark": Rule(reject_prev=_VERB_PREV | {"a", "the", "new"},
                  reject_next=frozenset({"interest", "curiosity", "innovation", "joy", "creativity",
                                         "change", "ideas", "conversation", "excitement"})),
    "monitoring": Rule(require_cue=frozenset({
        "system", "systems", "infrastructure", "application", "applications", "alert", "alerts",
        "alerting", "log", "logs", "logging", "observability", "prometheus", "grafana", "cloud",
        "network", "performance", "uptime", "server", "servers", "dashboard", "dashboards",
        "datadog", "nagios", "splunk", "kubernetes", "incident", "devops"})),
    "networking": Rule(require_cue=frozenset({
        "tcp", "ip", "routing", "switching", "dns", "vpn", "lan", "wan", "cisco", "firewall",
        "firewalls", "protocols", "infrastructure", "cloud", "systems", "security", "router",
        "routers", "switch", "switches", "cybersecurity", "ccna", "vlan"})),
    "agile": Rule(window=3, require_cue=frozenset({
        "scrum", "kanban", "sprint", "sprints", "methodology", "methodologies", "environment",
        "environments", "development", "software", "delivery", "team", "teams", "framework",
        "frameworks", "practices", "devops", "jira"})),
    "containers": Rule(require_cue=frozenset({
        "docker", "kubernetes", "k8s", "orchestration", "linux", "deploy", "deploying",
        "deployment", "images", "cloud", "microservices", "devops", "application",
        "applications", "package", "packages", "run"})),
    "documentation": Rule(window=3, require_cue=frozenset({
        "technical", "api", "apis", "software", "system", "systems", "code", "user", "product",
        "write", "writing", "readme", "confluence", "wiki"})),
    "torch": Rule(require_cue=frozenset({
        "deep", "learning", "neural", "network", "networks", "tensor", "tensors", "cuda", "gpu",
        "model", "models", "pytorch", "train", "training"})),
}

# Bare tokens the dictionary never matches alone: normalized form -> required raw spelling.
BARE_TOKENS = {"r": "R", "go": "Go"}
_BARE_PREV = frozenset({"in", "with", "using", "of", "for"})
_RND_RE = re.compile(r"\s*[&/]\s*[Dd]\b")  # R&D, R/D
_LIST_GAP_RE = re.compile(r"^[\s,;/&+|]*$")
_CONJ = frozenset({"and", "or"})

# ------------------------------------------------------------------ requirement level
_PREFERRED_RE = re.compile(
    r"\b(nice[- ]to[- ]have|good[- ]to[- ]have|preferred|desirable|bonus points?|"
    r"(?:is|are|be|as)\s+(?:an?\s+)?(?:added\s+)?(?:plus|advantage|asset)|"
    r"an?\s+(?:added\s+)?(?:plus|advantage|asset))\b", re.IGNORECASE)
_REQUIRED_RE = re.compile(r"\b(must|required|essential|mandatory|minimum)\b", re.IGNORECASE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _SENT_SPLIT_RE.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def requirement_level(sentence: str) -> str:
    pref = bool(_PREFERRED_RE.search(sentence))
    req = bool(_REQUIRED_RE.search(sentence))
    if pref and not req:
        return "preferred"
    if req and not pref:
        return "required"
    return "unspecified"


# ------------------------------------------------------------------ extractor
@dataclass(frozen=True)
class Extraction:
    skill_id: str
    canonical_name: str
    evidence: str          # raw text that matched (first occurrence)
    rule: str              # phrase | context_ok | context_list | bare_prep | bare_list
    requirement_level: str
    importance: float
    start: int
    end: int
    mentions: int = field(default=1)


class SkillExtractor:
    def __init__(self, dictionary: SkillDictionary | None = None):
        self.dictionary = dictionary or SkillDictionary.load()
        self._bare = {form: self.dictionary.lookup(form) for form in BARE_TOKENS}

    # --- rule helpers
    @staticmethod
    def _weak_check(rule: Rule, toks: list[Tok], i: int, sentence_start: int) -> str:
        """'reject' (verb/other non-skill context), 'accept', or 'defer' (no cue word nearby;
        may still be accepted later if it sits in a list next to an accepted skill)."""
        prev = toks[i - 1].norm if i > 0 else None
        nxt = toks[i + 1].norm if i + 1 < len(toks) else None
        if prev in rule.reject_prev or nxt in rule.reject_next:
            return "reject"
        if rule.initial_reject_next and toks[i].start == sentence_start and nxt in rule.initial_reject_next:
            return "reject"
        if rule.require_cue:
            lo, hi = max(0, i - rule.window), min(len(toks), i + rule.window + 1)
            if not any(toks[j].norm in rule.require_cue for j in range(lo, hi) if j != i):
                return "defer"
        return "accept"

    @staticmethod
    def _list_neighbor(text: str, toks: list[Tok], i: int, step: int, covered: set[int]) -> bool:
        """Is the nearest non-conjunction token in direction ``step`` an accepted skill,
        separated from toks[i] only by list punctuation (and/or allowed)?"""
        j = i + step
        while 0 <= j < len(toks) and toks[j].norm in _CONJ:
            j += step
        if not 0 <= j < len(toks) or j not in covered:
            return False
        a, b = (toks[j].end, toks[i].start) if step < 0 else (toks[i].end, toks[j].start)
        gap = re.sub(r"\b(?:and|or)\b", " ", text[a:b], flags=re.IGNORECASE)
        return bool(_LIST_GAP_RE.match(gap))

    def extract(self, text: str) -> list[Extraction]:
        toks = tokenize(text)
        sent_spans = _sentence_spans(text)
        sent_starts = [s for s, _ in sent_spans]

        def sentence_of(pos: int) -> tuple[int, int]:
            return sent_spans[max(0, bisect_right(sent_starts, pos) - 1)]

        # 1-2. dictionary candidates, minus risky single-word matches that fail their rule
        accepted: list[tuple[int, int, str, str]] = []  # (tok_start, tok_end, skill_id, rule)
        deferred: list[tuple[int, int, str]] = []
        covered: set[int] = set()
        for a, b, sid in self.dictionary.match_tokens([t.norm for t in toks]):
            rule_name = "phrase"
            if b - a == 1 and toks[a].norm in WEAK_RULES:
                s0, _ = sentence_of(toks[a].start)
                verdict = self._weak_check(WEAK_RULES[toks[a].norm], toks, a, s0)
                if verdict == "reject":
                    continue
                if verdict == "defer":
                    deferred.append((a, b, sid))
                    continue
                rule_name = "context_ok"
            accepted.append((a, b, sid, rule_name))
            covered.update(range(a, b))
        # deferred words are accepted when list-adjacent to an accepted skill (repeat until stable)
        changed = True
        while changed and deferred:
            changed = False
            for item in list(deferred):
                a, b, sid = item
                if (self._list_neighbor(text, toks, a, -1, covered)
                        or self._list_neighbor(text, toks, a, +1, covered)):
                    accepted.append((a, b, sid, "context_list"))
                    covered.update(range(a, b))
                    deferred.remove(item)
                    changed = True

        # 3. bare 'R' / 'Go'
        taken = set(covered)
        for i, t in enumerate(toks):
            need = BARE_TOKENS.get(t.norm)
            if need is None or t.raw != need or i in taken:
                continue
            if _RND_RE.match(text[t.end:]):
                continue
            via = None
            if i > 0 and toks[i - 1].norm in _BARE_PREV:
                via = "bare_prep"
            elif (self._list_neighbor(text, toks, i, -1, covered)
                  or self._list_neighbor(text, toks, i, +1, covered)):
                via = "bare_list"
            if via:
                accepted.append((i, i + 1, self._bare[t.norm].skill_id, via))

        # 4. aggregate per skill with requirement level
        accepted.sort(key=lambda m: m[0])
        by_skill: dict[str, Extraction] = {}
        for a, b, sid, rule_name in accepted:
            start, end = toks[a].start, toks[b - 1].end
            s0, s1 = sentence_of(start)
            level = requirement_level(text[s0:s1])
            imp = IMPORTANCE_PREFERRED if level == "preferred" else IMPORTANCE_DEFAULT
            prior = by_skill.get(sid)
            if prior is None:
                by_skill[sid] = Extraction(sid, self.dictionary.skills[sid].canonical_name,
                                           text[start:end], rule_name, level, imp, start, end)
            else:
                better = imp > prior.importance
                by_skill[sid] = Extraction(
                    sid, prior.canonical_name, prior.evidence, prior.rule,
                    level if better else prior.requirement_level,
                    imp if better else prior.importance,
                    prior.start, prior.end, prior.mentions + 1)
        return sorted(by_skill.values(), key=lambda e: e.start)

    def extract_ids(self, text: str) -> list[str]:
        return [e.skill_id for e in self.extract(text)]
