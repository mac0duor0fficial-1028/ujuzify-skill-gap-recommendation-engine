"""Deterministic, context-aware career coach (no external model required).

Answers are grounded in the user's latest analysis and in the governed taxonomy and catalogues.
CV bullet templates deliberately use [placeholders] for facts the user must supply: the coach never
invents achievements or metrics.
"""
from __future__ import annotations

import re

from src.nlp.hybrid_skill_extractor import get_extractor
from src.product import resource_recommender as rr
from src.taxonomy import get_taxonomy

_QUESTIONS = {
    "databases": ["Explain how you would find and fix a slow query. What would you check first?", "What is the difference between an INNER JOIN and a LEFT JOIN? Give a business example.", "How do indexes help, and what is their cost?"],
    "analytics": ["Walk me through how you would clean and validate a messy dataset before reporting on it.", "A stakeholder disputes a number on your dashboard. How do you investigate?", "How would you choose between a bar chart, a line chart and a table for a given insight?"],
    "ai_ml": ["How do you detect and handle overfitting?", "Explain precision versus recall and when you would optimise for each.", "How would you take a notebook model to production and monitor it?"],
    "data_engineering": ["How would you design an idempotent daily pipeline that can be safely re-run?", "What checks do you add to catch bad data before it reaches analysts?", "Batch versus streaming: how do you choose?"],
    "cloud": ["How would you design a secure, highly available web application on a public cloud?", "What is the difference between a security group and a network ACL?", "How do you keep cloud costs under control?"],
    "devops": ["Describe a CI/CD pipeline you would build for a small web service.", "What happens when you run 'docker run'? Walk through the layers.", "How do you roll back a bad deployment?"],
    "software_engineering": ["How do you decide what to unit test versus integration test?", "Design a URL shortener: outline the components and trade-offs.", "How do you keep a REST API backwards compatible?"],
    "security": ["Walk me through how you would triage a suspicious login alert.", "What is the difference between a vulnerability, a threat and a risk?", "How would you investigate a possible phishing incident?"],
    "programming": ["Describe a program you wrote that you are proud of. What would you change today?", "How do you handle errors and edge cases in your code?", "Explain the difference between a list and a dictionary and when to use each."],
    "professional": ["Tell me about a time you explained a technical idea to a non-technical person.", "Describe a deadline you nearly missed and what you did.", "How do you prioritise when everything is urgent?"],
}


def _ctx(context: dict | None):
    c = context or {}
    a = c.get("analysis") or {}
    return {
        "role": (c.get("current_role") or "").strip(), "level": c.get("experience_level") or "Mid",
        "skills": [s for s in (c.get("skills") or []) if s],
        "target": (a.get("targetRoleTitle") or c.get("target_role") or "").strip().split("\n")[0][:80],
        "score": a.get("readinessScore"), "matched": a.get("matchedSkills") or [],
        "partial": a.get("partialSkills") or [], "missing": a.get("missingSkills") or [],
        "roadmap": a.get("roadmap") or [], "summary": a.get("executiveSummary") or "", "has": bool(a),
    }


def _find_skill(message: str, c: dict) -> tuple[str | None, str | None]:
    """Find a skill the user is asking about: first among their gaps, then anywhere in the taxonomy."""
    tax = get_taxonomy()
    m = message.lower()
    for name in c["missing"] + c["partial"] + c["matched"]:
        if name.lower() in m:
            return tax.resolve(name), name
    found = get_extractor().extract(message)
    if found:
        return found[0].canonical_name, tax.display(found[0].canonical_name)
    return None, None


def _skill_plan(canon: str | None, name: str, c: dict) -> str:
    rs = rr.resources_for_skill(canon, 3) if canon else []
    proj = rr.project_for_skill(canon) if canon else None
    lines = [f"### One-week plan: {name}", ""]
    days = [
        ("Day 1", f"Map the territory: skim the overview of {name} and write down 5 questions you want answered."),
        ("Days 2-3", f"Work through the first {'resource below' if rs else 'tutorial you can find'} hands-on; type every example, do not just read."),
        ("Day 4", "Rebuild one example from memory, without looking, to expose what you have not really learned."),
        ("Days 5-6", f"Apply it: {proj.title + ' - ' + proj.description if proj else 'build a tiny project that uses it on data or a problem you care about'}"),
        ("Day 7", "Write a short README: what you built, what you learned, what is still shaky. This becomes portfolio evidence."),
    ]
    lines += [f"- **{d}:** {t}" for d, t in days]
    if rs:
        lines += ["", "**Start with:**"] + [f"- [{r.title}]({r.url}) ({r.cost_tier}, about {int(r.hours)} h)" for r in rs]
    if c["target"]:
        lines += ["", f"This closes a gap for **{c['target']}**." if name in c["missing"] + c["partial"] else f"This reinforces your fit for **{c['target']}**."]
    return "\n".join(lines)


def _sprint(c: dict) -> str:
    if c["roadmap"]:
        first = c["roadmap"][0]
        name = re.sub(r"^(?:Strengthen|Advance|Elevate)\s+", "", first["skill"])
        return _skill_plan(get_taxonomy().resolve(name), name, c)
    if c["missing"]:
        n = c["missing"][0]
        return _skill_plan(get_taxonomy().resolve(n), n, c)
    return ("Run **Analyze Skill Gap** first so I can pick your top gap, then ask again for a focused 7-day sprint. "
            "Or name a skill, for example: *plan for Docker*.")


def _cv(c: dict) -> str:
    if not c["has"] and not c["skills"]:
        return "Add your skills (or upload a CV) and a target role, then ask again so I can tailor bullets."
    skills = c["matched"][:4] or c["skills"][:4]
    target = c["target"] or "your target role"
    sk = lambda i: skills[i] if i < len(skills) else (skills[0] if skills else "[skill]")  # noqa: E731
    out = [f"### Resume bullet templates for {target}", "",
           "Fill every [bracket] with a real fact. Do not keep a number you cannot defend in an interview.", "",
           f"- Built [what] using **{sk(0)}** and **{sk(1)}**, reducing [problem] by [measured result] for [who].",
           f"- Used **{sk(1)}** to analyse [dataset/system] and presented [finding] to [audience], leading to [decision].",
           f"- Automated [manual task] with **{sk(2)}**, saving about [hours] per [period] and cutting errors by [percent].",
           f"- Documented and tested [component] with **{sk(0)}** so that [teammates/users] could [outcome]."]
    if c["missing"]:
        out += ["", f"Do not claim **{', '.join(c['missing'][:3])}** yet. Build a small project first, then add a truthful bullet."]
    return "\n".join(out)


def _interview(c: dict) -> str:
    tax = get_taxonomy()
    cats: list[str] = []
    for n in c["matched"] + c["partial"] + c["missing"]:
        canon = tax.resolve(n)
        cat = tax.category_of(canon) if canon else None
        if cat and cat not in cats and cat in _QUESTIONS:
            cats.append(cat)
    cats = cats[:3] or ["programming", "professional"]
    qs = [_QUESTIONS[cat][i] for i, cat in enumerate(cats)]
    qs.append(_QUESTIONS["professional"][0])
    out = [f"### Mock interview: {c['target'] or 'technology role'}", ""] + [f"{i}. {q}" for i, q in enumerate(qs, 1)]
    out += ["", "Answer out loud in 90 seconds each: situation, what you did, the result, what you would change. "
            "Paste an answer here and I will point out what a reviewer would probe."]
    return "\n".join(out)


def _motivation() -> str:
    return ("### Pacing yourself\n\nUpskilling while working is a marathon. A sustainable rhythm beats a heroic week:\n\n"
            "- **Fix a small, fixed block:** 45-60 minutes on 4 days a week is better than one 6-hour Sunday.\n"
            "- **One skill at a time.** Switching topics is what makes it feel endless.\n"
            "- **Ship something small every week** (a query, a dashboard, a repo). Visible progress is the best motivation.\n"
            "- **Plan rest on purpose,** and drop the lowest-priority roadmap item if a week goes badly.\n\n"
            "If the stress feels bigger than a busy schedule, please talk to someone you trust.")


def _why(c: dict) -> str:
    if not c["has"]:
        return "Run **Analyze Skill Gap** first, then ask me to explain the score."
    return (f"### How your {c['score']}% was calculated\n\n"
            "Each requirement has a weight. A skill you hold earns its full weight, a related skill earns half, a missing skill earns nothing. "
            "Your score is the earned weight divided by the total. For the supported technology careers a small adjustment (15%) "
            "reflects how often those skills appear in sampled job postings, when posting data is available.\n\n"
            f"- Matched: {', '.join(c['matched'][:6]) or 'none'}\n- Needs depth: {', '.join(c['partial'][:6]) or 'none'}\n"
            f"- Missing: {', '.join(c['missing'][:6]) or 'none'}\n\n"
            "This is a compatibility indicator, **not** a probability of getting hired.")


def _resources(c: dict) -> str:
    names = (c["missing"] + c["partial"])[:3]
    if not names:
        return "Tell me which skill you want to learn (for example *resources for SQL*) and I will list free options first."
    out = ["### Where to start", ""]
    for n in names:
        canon = get_taxonomy().resolve(n)
        rs = rr.resources_for_skill(canon, 2) if canon else []
        out.append(f"**{n}**")
        out += [f"- [{r.title}]({r.url}) ({r.cost_tier}, about {int(r.hours)} h)" for r in rs] or ["- Search the official documentation and a hands-on tutorial."]
    return "\n".join(out)


def _next_step(c: dict) -> str:
    if not c["roadmap"]:
        return "Run **Analyze Skill Gap** first; I will then recommend the single most valuable next step."
    f = c["roadmap"][0]
    return (f"### Your best next step\n\nStart with **{f['skill']}** ({f['estimatedTime']}, {f['priority']} priority).\n\n"
            f"**Deliverable:** {f['projectIdea']}\n\nAsk me for a 7-day sprint on it when you are ready.")


def _skill_info(canon: str, name: str) -> str:
    tax = get_taxonomy()
    careers = sorted(((c.skills[canon], c.name) for c in tax.careers.values() if canon in c.skills), reverse=True)[:4]
    rs = rr.resources_for_skill(canon, 2)
    out = [f"### {name}", ""]
    if careers:
        out.append("It matters most for: " + ", ".join(f"{n} ({w:.0%} weight)" for w, n in careers) + ".")
    rel = [tax.display(r) for r in tax.related(canon)]
    if rel:
        out.append(f"Related skills that transfer: {', '.join(rel)}.")
    if rs:
        out += ["", "Learn it with:"] + [f"- [{r.title}]({r.url}) ({r.cost_tier})" for r in rs]
    out += ["", f"Say *plan for {name}* for a one-week sprint."]
    return "\n".join(out)


def reply(message: str, history: list | None = None, context: dict | None = None) -> dict:
    m = (message or "").strip()
    if not m:
        raise ValueError("Message is required.")
    low = m.lower()
    c = _ctx(context)
    canon, name = _find_skill(m, c)

    if re.search(r"\b(hi|hello|hey|help|what can you do)\b", low) and len(low.split()) <= 6:
        text = ("Hi, I'm the Ujuzify Coach. I can explain your readiness score, build a 7-day sprint, tailor CV bullets, "
                "run a mock interview, suggest learning resources, or help you pace your upskilling.\n\n"
                + (f"You are targeting **{c['target']}**. " if c["target"] else "Run an analysis to unlock tailored advice. ")
                + "What would you like to do?")
    elif re.search(r"overwhelm|motivat|stress|burn|pace|procrastinat|tired|discourag", low):
        text = _motivation()
    elif re.search(r"\b(cv|resume|bullet)", low):
        text = _cv(c)
    elif re.search(r"interview|mock", low):
        text = _interview(c)
    elif re.search(r"\b(7[- ]day|seven[- ]day|sprint|study plan|1[- ]week|one[- ]week|week plan|plan for|roadmap for)\b", low):
        text = _skill_plan(canon, name, c) if name else _sprint(c)
    elif re.search(r"why|score|readiness|calculated|explain|how .* (?:work|compute)", low) and c["has"] and not name:
        text = _why(c)
    elif re.search(r"resource|course|where (?:can|do) i learn|learn|tutorial|book", low):
        text = _skill_info(canon, name) if name and not (c["missing"] + c["partial"]) else _resources(c) if not name else _skill_info(canon, name)
    elif re.search(r"salary|pay\b|how much (?:will|do)", low):
        text = ("I can't predict salaries or guarantee outcomes, and the Ujuzify score is not an employment probability. "
                "Check current ranges for your city on job boards and ask recruiters; then I can help you negotiate or close skill gaps that raise your market value.")
    elif re.search(r"next|start|first|priorit|should i", low):
        text = _next_step(c)
    elif name:
        text = _skill_info(canon, name)
    elif c["has"]:
        text = (f"{c['summary']}\n\nYou can ask me: *why is my score {c['score']}%*, *plan for {(c['missing'] or ['a skill'])[0]}*, "
                "*tailor CV bullets*, *mock interview*, or *what should I do next*.")
    else:
        text = ("I'm tuned for career questions: skill gaps, learning plans, CV bullets and interview practice. "
                "Run **Analyze Skill Gap** so I can ground my advice in your profile, or ask something like *plan for Docker*.")
    return {"reply": text, "sources": [], "engine": "ujuzify-rule-coach"}
