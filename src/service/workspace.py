"""Backend for the Ujuzify web interface: resume parsing, job extraction and skill-gap analysis.

All results are produced by the deterministic, taxonomy-governed engine. Nothing is guessed:
skills are recognised only from approved taxonomy terms or from explicit requirement text.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from src import config
from src.nlp.hybrid_skill_extractor import get_extractor
from src.product import resource_recommender as rr
from src.recommendation.recommendation_engine import get_engine
from src.service.documents import document_text, detect_unavailable
from src.service.errors import EngineError
from src.service.job_parser import extract_title, fetch_job_url, parse_job_text
from src.taxonomy import CATEGORY_LABELS, get_taxonomy, normalize_text

_STOP = {"and", "or", "the", "of", "in", "with", "for", "to", "a", "an", "skills", "skill", "experience", "knowledge"}


def _tokens(s: str) -> set[str]:
    return {t for t in normalize_text(s).split() if t not in _STOP and len(t) > 1}


# ----------------------------------------------------------------------------- resume
def parse_resume(doc: dict) -> dict:
    text = document_text(doc)
    if not text:
        raise EngineError("Could not read content from the uploaded resume file.", 400)
    ext, tax = get_extractor(), get_taxonomy()
    skills = [tax.display(m.canonical_name) for m in ext.extract(text)]
    head = "\n".join(text.splitlines()[:25])
    career = tax.match_career_title(head)
    role = ""
    for line in [l.strip() for l in head.splitlines() if l.strip()][:15]:
        if re.search(r"(?i)\b(engineer|developer|analyst|scientist|administrator|architect|intern|technician|officer|consultant|specialist)\b", line) and len(line.split()) <= 8:
            role = line
            break
    if not role and career:
        role = career.name
    yrs = [int(x) for x in re.findall(r"(\d{1,2})\s*\+?\s*years", text.lower())]
    y = max(yrs) if yrs else 0
    senior = "Lead" if y >= 8 else "Senior" if y >= 5 else "Mid" if y >= 2 else "Entry"
    if re.search(r"(?i)\b(?:intern|graduate|student|entry[- ]level|junior)\b", head):
        senior = "Entry"
    return {"detectedRole": role, "detectedSeniority": senior, "detectedSkills": skills[:24],
            "summaryText": text[:4000]}


# --------------------------------------------------------------------------- extraction
def extract_job(url: str | None, text: str | None, document: dict | None) -> dict:
    if document and (document.get("base64Data") or document.get("textContent")):
        body = document_text(document)
        if not body:
            raise EngineError("The uploaded posting contains no readable text.", 422)
        return parse_job_text(body, source=f"Document ({document.get('name') or 'upload'})")
    if url and re.match(r"(?i)^https?://", url.strip()):
        fetched = fetch_job_url(url)
        gone = detect_unavailable(fetched["text"])
        if gone:
            raise EngineError(gone[1], 410 if gone[0] == "expired" else 404, "JOB_UNAVAILABLE", {"postingStatus": gone[0]})
        res = parse_job_text(fetched["text"], title_hint=fetched.get("title", ""), source="Direct Server Fetch")
        res["company"], res["location"] = fetched.get("company", ""), fetched.get("location", "")
        return res
    if text and len(text.strip()) >= 3:
        return parse_job_text(text, source="Text Extraction")
    raise EngineError("Provide a valid job posting URL, target description text, or document.", 400)


# ------------------------------------------------------------------------------ analysis
@dataclass
class Req:
    name: str            # display name
    key: str             # canonical id, or normalised phrase for free-text skills
    canon: str | None
    weight: float
    kind: str            # 'req' | 'pref'
    pillar: str = ""
    status: str = "missing"  # matched | partial | missing
    via: str = ""


def _requirements(tax, extracted: dict | None, jd_text: str, target_title: str) -> tuple[list[Req], str, str | None]:
    """Return (requirements, resolved title, career_id used for weights if any)."""
    engine = get_engine()
    career = None
    title = ""
    reqs: list[Req] = []

    parsed = None
    if extracted and (extracted.get("requiredSkills") or extracted.get("preferredSkills")):
        parsed = extracted
        title = str(extracted.get("jobTitle") or "").strip()
    elif jd_text and len(jd_text.split()) > 12:
        try:
            parsed = parse_job_text(jd_text, source="Text Extraction")
            title = parsed["jobTitle"]
        except EngineError as exc:
            if exc.code != "NO_SKILLS_FOUND":
                raise

    title = title or extract_title(jd_text, target_title) if (jd_text or target_title) else title
    career = tax.match_career_title(title) or tax.match_career_title(target_title)

    if parsed:
        for kind, key, base in (("req", "requiredSkills", 1.0), ("pref", "preferredSkills", 0.5)):
            for raw in parsed.get(key, []) or []:
                canon = tax.resolve(raw)
                w = base
                if canon and career and canon in career.skills:
                    w = career.skills[canon] * (1.0 if kind == "req" else 0.7)
                elif not canon:
                    w = base * 0.7
                reqs.append(Req(tax.display(canon) if canon else str(raw), canon or normalize_text(raw), canon, w, kind))
        pillars = parsed.get("competencyPillars") or []
        by_name = {}
        for p in pillars:
            for s in p.get("skills", []) or []:
                by_name[normalize_text(s)] = str(p.get("area") or "Competency")
        for r in reqs:
            r.pillar = by_name.get(normalize_text(r.name)) or (
                CATEGORY_LABELS.get(tax.category_of(r.canon) or "", "") if r.canon else "") or "Domain Knowledge"
    elif career:
        for s, w in career.skills.items():
            reqs.append(Req(tax.display(s), s, s, w, "req", CATEGORY_LABELS.get(tax.category_of(s) or "", "Core Requirements")))
        title = title or career.name
    # de-duplicate by key (keep highest weight)
    seen: dict[str, Req] = {}
    for r in reqs:
        if r.key not in seen or r.weight > seen[r.key].weight:
            seen[r.key] = r
    return list(seen.values()), title, career.career_id if career else None


def analyze(payload: dict) -> dict:
    tax, ext, engine = get_taxonomy(), get_extractor(), get_engine()
    raw_skills = payload.get("skills") or []
    owned_known, owned_unknown = tax.parse_skill_list(raw_skills)

    resume_text = document_text(payload.get("resume")) if payload.get("resume") else ""
    cv_skills = ext.skills(resume_text) if resume_text else []
    for s in cv_skills:
        if s not in owned_known:
            owned_known.append(s)
    resume_norm = normalize_text(resume_text)

    target_role = str(payload.get("targetRole") or "").strip()
    jd_text = target_role
    jd_doc = payload.get("targetJobDocument")
    if jd_doc and (jd_doc.get("base64Data") or jd_doc.get("textContent")):
        jd_text = (jd_text + "\n" + document_text(jd_doc)).strip()
    if not owned_known and not owned_unknown:
        raise EngineError("Add at least one current skill or attach a CV so the gap can be calculated.", 422)
    gone = detect_unavailable(target_role) if len(target_role) < 1600 else None
    if gone:
        raise EngineError(gone[1], 410 if gone[0] == "expired" else 404, "JOB_UNAVAILABLE", {"postingStatus": gone[0]})

    reqs, title, career_id = _requirements(tax, payload.get("extractedTarget"), jd_text, target_role)
    if not reqs:
        raise EngineError(
            "Could not determine the requirements for that target role. Enter a supported technology role "
            "(e.g. Data Analyst, Data Engineer, Cloud Engineer) or paste the posting's requirements.", 422, "NO_REQUIREMENTS")

    owned_set = set(owned_known)
    unknown_norm = [normalize_text(u) for u in owned_unknown]
    for r in reqs:
        if r.canon:
            if r.canon in owned_set:
                r.status = "matched"
                continue
            rel = [x for x in tax.related(r.canon) if x in owned_set]
            if rel:
                r.status, r.via = "partial", tax.display(rel[0])
        else:
            toks = _tokens(r.name)
            if r.key in unknown_norm or (resume_norm and re.search(rf"(?<![a-z0-9]){re.escape(r.key)}(?![a-z0-9])", resume_norm)):
                r.status = "matched"
                continue
            best = 0.0
            for u in unknown_norm:
                ut = _tokens(u)
                if toks and ut:
                    best = max(best, len(toks & ut) / len(toks | ut))
            hit = [s for s in ext.skills(r.name) if s in owned_set]
            if best >= 0.5 or hit:
                r.status, r.via = "partial", (tax.display(hit[0]) if hit else "")
    weights = {r.key: r.weight for r in reqs}
    matched = {r.key for r in reqs if r.status == "matched"}
    partial = {r.key for r in reqs if r.status == "partial"}
    coverage = engine.coverage(matched, weights, partial)

    market = None
    canon_w = {r.canon: r.weight for r in reqs if r.canon}
    if career_id and canon_w and len(canon_w) >= len(reqs) * 0.5:
        market = engine.market_adjustment(canon_w)
    score = engine.match_score(coverage, market)
    readiness = int(round(max(0.0, min(1.0, score)) * 100))

    # competency pillars
    pill: dict[str, dict] = {}
    for r in reqs:
        p = pill.setdefault(r.pillar or "Core Requirements", {"w": 0.0, "got": 0.0, "target": []})
        p["w"] += r.weight
        p["got"] += r.weight if r.status == "matched" else r.weight * config.PARTIAL_CREDIT if r.status == "partial" else 0
    level_target = {"Entry": 76, "Mid": 85, "Senior": 90, "Lead": 92}.get(
        str((payload.get("extractedTarget") or {}).get("seniority") or payload.get("experienceLevel") or "Mid"), 85)
    given = {normalize_text(p.get("area", "")): p.get("targetScore") for p in ((payload.get("extractedTarget") or {}).get("competencyPillars") or [])}
    competency = []
    for area, p in sorted(pill.items(), key=lambda kv: -kv[1]["w"])[:6]:
        tgt = given.get(normalize_text(area)) or level_target
        competency.append({"area": area, "current": int(round(100 * p["got"] / p["w"])) if p["w"] else 0,
                           "target": int(max(10, min(100, tgt)))})

    # roadmap
    def prio(r: Req) -> float:
        d = engine.demand.get(r.canon) if (engine.demand and r.canon) else None
        return engine.gap_priority(r.weight, d) * (0.7 if r.status == "partial" else 1.0)

    gaps = sorted([r for r in reqs if r.status != "matched"], key=lambda r: (-prio(r), r.name))
    steps = gaps[:6]
    roadmap = []
    for i, r in enumerate(steps):
        weeks = rr.estimate_weeks(r.canon) if r.canon else 2
        if r.status == "partial":
            weeks = max(1, math.ceil(weeks * 0.6))
        proj = rr.project_for_skill(r.canon, career_id) if r.canon else None
        idea = (f"{proj.title}: {proj.description}" if proj else
                f"Build a small portfolio deliverable that applies {r.name} to a realistic {title} task and document the result.")
        resources = [{"title": x.title, "url": x.url, "tier": x.cost_tier} for x in rr.resources_for_skill(r.canon, 3)] if r.canon else []
        roadmap.append({
            "skill": f"Strengthen {r.name}" if r.status == "partial" else r.name,
            "priority": "High" if (i < math.ceil(len(steps) / 2) or r.weight >= 0.85) else "Medium",
            "estimatedTime": f"{weeks} week{'s' if weeks != 1 else ''}",
            "projectIdea": idea, "resources": resources,
            "reason": f"{r.name} has weight {r.weight:.2f} for this role" + (f"; you already know {r.via}, which transfers." if r.via and r.status == 'partial' else "."),
        })

    m_names = [r.name for r in sorted(reqs, key=lambda r: -r.weight) if r.status == "matched"]
    x_names = [r.name for r in gaps]
    total = len(reqs)
    if m_names:
        s1 = f"You already cover {len(m_names)} of {total} requirements for {title}, led by {', '.join(m_names[:3])}."
    else:
        s1 = f"None of the {total} requirements for {title} are covered yet, so this is a fresh start with a clear order of attack."
    s2 = (f"The main bottleneck is {', '.join(x_names[:3])}, which the roadmap schedules first." if x_names
          else "No gaps were found against the stated requirements, so focus on evidence and interview preparation.")

    ranked = engine.rank_careers(owned_known)[:3] if owned_known else []
    return {
        "targetRoleTitle": title, "readinessScore": readiness, "executiveSummary": f"{s1} {s2}",
        "matchedSkills": [r.name for r in reqs if r.status == "matched"],
        "partialSkills": [r.name for r in reqs if r.status == "partial"],
        "missingSkills": [r.name for r in reqs if r.status == "missing"],
        "competencyScores": competency, "roadmap": roadmap,
        "engine": {
            "modelVersion": config.MODEL_VERSION, "coverage": round(coverage, 4),
            "marketAdjustment": None if market is None else round(market, 4),
            "demandDataUsed": market is not None, "careerId": career_id,
            "scoreNote": "Readiness is a compatibility indicator, not a probability of employment.",
            "topCareers": [{"careerId": m.career_id, "name": m.career_name, "score": m.match_score} for m in ranked],
            "recognisedSkills": [tax.display(s) for s in owned_known], "unrecognisedSkills": owned_unknown,
        },
    }
