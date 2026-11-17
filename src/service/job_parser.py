"""Parse job-posting text (pasted, uploaded or fetched) into structured, taxonomy-governed requirements."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

from src.nlp.hybrid_skill_extractor import get_extractor
from src.service.documents import detect_unavailable, strip_html
from src.service.errors import EngineError
from src.taxonomy import CATEGORY_LABELS, get_taxonomy, normalize_text

_H_REQ = re.compile(r"^(?:key\s+)?(?:requirements?|qualifications?|minimum qualifications?|what you(?:'ll| will)? need|what we(?:'re| are) looking for|must[- ]haves?|skills?(?: and experience| required| & experience)?|you have|about you|required skills?|essential)\b", re.I)
_H_PREF = re.compile(r"^(?:preferred(?: qualifications| skills)?|nice[- ]to[- ]haves?|bonus(?: points)?|desirable|good to have|a plus|extra credit|plus)\b", re.I)
_H_RESP = re.compile(r"^(?:key\s+)?(?:responsibilit(?:y|ies)|duties|what you(?:'ll| will)? do|the role|your role|day[- ]to[- ]day|key tasks|role overview)\b", re.I)
_H_OTHER = re.compile(r"^(?:about(?: us| the (?:company|team|role))?|benefits|what we offer|perks|compensation|equal opportunity|how to apply|company overview|who we are|why join)\b", re.I)
_BOILER = re.compile(r"^(?:about|job\s+description|job\s+summary|position\s+overview|role\s+overview|company\s+overview|who\s+we\s+are|responsibilities|requirements|qualifications|benefits|location|department|apply)\b", re.I)
_PREFIX = re.compile(r"^(?:experience (?:with|in|using)|proficiency (?:with|in)|knowledge of|expertise in|familiarity with|strong (?:background|knowledge) (?:in|of)|ability to|understanding of|hands[- ]on experience (?:with|in)|working knowledge of|solid understanding of)\s+", re.I)


def _is_heading(line: str) -> str | None:
    s = re.sub(r"^[#*\s>_-]+|[*:\s_]+$", "", line)
    if not s or len(s) > 70:
        return None
    if _H_PREF.match(s):
        return "pref"
    if _H_REQ.match(s):
        return "req"
    if _H_RESP.match(s):
        return "resp"
    if _H_OTHER.match(s):
        return "other"
    return None


def clean_title(raw: str) -> str:
    t = re.sub(r"^#+\s*", "", raw.strip())
    t = re.sub(r"^\*{1,2}([^*]+)\*{1,2}$", r"\1", t)
    t = re.sub(r"^(?:job\s*title|role\s*title|position\s*title|title|role|position|opening)\s*[:\-–—]\s*", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:remote|hybrid|on[- ]?site|full[- ]?time|part[- ]?time|contract|[^)]{0,32},\s*[A-Z]{2})\)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+\|\s+[^|]{2,40}$", "", t)
    t = re.sub(r"\s+at\s+[A-Z][\w&.,' -]{1,40}$", "", t)
    t = re.sub(r"\s+(?:requiring|with\s+\d+\+?)\s+.+$", "", t, flags=re.I)
    return t.strip(" -–—|:")


def extract_title(text: str, hint: str = "") -> str:
    if hint and not _BOILER.match(clean_title(hint)) and not re.match(r"https?://", hint):
        return clean_title(hint)[:80]
    tax = get_taxonomy()
    for line in [l.strip() for l in text.splitlines() if l.strip()][:12]:
        c = clean_title(line)
        if 3 <= len(c) <= 80 and not _BOILER.match(c) and not re.match(r"https?://", c) and len(c.split()) <= 9:
            if tax.match_career_title(c) or re.search(r"(?i)\b(engineer|developer|analyst|scientist|manager|designer|administrator|architect|specialist|officer|lead|consultant|technician|intern|associate|accountant|nurse|teacher)\b", c):
                return c
    first = next((clean_title(l) for l in text.splitlines() if l.strip()), "")
    return first[:80] if first and not _BOILER.match(first) and len(first.split()) <= 9 else "Target Role"


def detect_seniority(title: str, text: str) -> str:
    t = f"{title}\n{text[:2500]}".lower()
    if re.search(r"\b(?:lead|principal|head of|director|manager|architect)\b", title.lower()):
        return "Lead"
    if re.search(r"\b(?:senior|sr\.?|staff)\b", title.lower()):
        return "Senior"
    if re.search(r"\b(?:junior|jr\.?|entry[- ]level|graduate|intern(?:ship)?|trainee|apprentice)\b", t):
        return "Entry"
    m = re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d+\s*)?years?", t)
    if m:
        y = max(int(x) for x in m)
        return "Lead" if y >= 8 else "Senior" if y >= 5 else "Mid" if y >= 2 else "Entry"
    return "Mid"


def _bullet_phrases(line: str) -> list[str]:
    """Short atomic phrases from a 'Skills: a, b, c' style line or a short bullet."""
    s = re.sub(r"^[-*•\d.)\s]+", "", line).strip()
    s = _PREFIX.sub("", s)
    if ":" in s and len(s.split(":")[0].split()) <= 3:
        s = s.split(":", 1)[1]
    if len(s.split()) > 14:
        return []
    out = []
    for p in re.split(r"[,;/]|\band\b|\bor\b", s):
        p = re.sub(r"\s*\([^)]{22,}\)", "", p).strip(" .-")
        if 2 <= len(p) <= 36 and len(p.split()) <= 4 and not re.match(r"^\d+\+?\s*years?", p, re.I):
            if not re.search(r"(?i)\b(?:we|our|you|will|ability|team player|years?|degree|bachelor|master|candidate|etc)\b", p):
                out.append(p)
    return out


def parse_job_text(text: str, title_hint: str = "", source: str = "Text Extraction") -> dict:
    """Return the structure consumed by the UI's target-role extraction panel."""
    tax, ext = get_taxonomy(), get_extractor()
    text = (text or "").strip()
    if not text:
        raise EngineError("Provide a job posting URL, description text, or document.", 400)
    gone = detect_unavailable(text)
    if gone:
        raise EngineError(gone[1], 410 if gone[0] == "expired" else 404, "JOB_UNAVAILABLE", {"postingStatus": gone[0]})

    title = extract_title(text, title_hint)
    lines = [l.rstrip() for l in text.splitlines()]
    section = "req"  # content before any heading is treated as requirements-bearing
    buckets: dict[str, list[str]] = {"req": [], "pref": [], "resp": [], "other": []}
    has_sections = False
    for line in lines:
        if not line.strip():
            continue
        h = _is_heading(line)
        if h:
            section, has_sections = h, True
            remainder = re.sub(r"^[^:]{0,60}:\s*", "", line) if ":" in line else ""
            if remainder.strip():
                buckets[section].append(remainder)
            continue
        buckets[section].append(line)

    def skills_in(block: list[str]) -> list[str]:
        return ext.skills("\n".join(block))

    req_canon = skills_in(buckets["req"])
    pref_canon = [s for s in skills_in(buckets["pref"]) if s not in req_canon]
    # Skills mentioned only in responsibilities still describe the job: treat as required unless preferred.
    resp_canon = [s for s in skills_in(buckets["resp"]) if s not in req_canon and s not in pref_canon]
    req_canon += resp_canon
    if not has_sections:
        req_canon = ext.skills(text)

    # free-text (non-taxonomy) skills from explicit skill lines / short bullets in requirement sections
    extras_req: list[str] = []
    for line in buckets["req"]:
        stripped = line.strip()
        is_bullet = bool(re.match(r"^[-*•\d]", stripped)) or re.match(r"^(?:skills?|tools?|technologies|stack)\s*:", stripped, re.I)
        if is_bullet:
            for p in _bullet_phrases(stripped):
                if not tax.resolve(p) and not ext.skills(p) and p.lower() not in {e.lower() for e in extras_req}:
                    extras_req.append(p)
    extras_pref = []
    for line in buckets["pref"]:
        for p in _bullet_phrases(line):
            if not tax.resolve(p) and not ext.skills(p) and p.lower() not in {e.lower() for e in extras_pref + extras_req}:
                extras_pref.append(p)

    req_names = [tax.display(s) for s in req_canon] + extras_req[: max(0, 14 - len(req_canon))]
    pref_names = [tax.display(s) for s in pref_canon] + extras_pref[: max(0, 6 - len(pref_canon))]
    req_names, pref_names = req_names[:14], pref_names[:6]

    responsibilities = []
    for line in buckets["resp"]:
        c = re.sub(r"^[-*•\d.)\s]+", "", line).strip()
        if 12 <= len(c) <= 200:
            responsibilities.append(c)
    responsibilities = responsibilities[:6]

    seniority = detect_seniority(title, text)
    target = {"Entry": 76, "Mid": 85, "Senior": 90, "Lead": 92}[seniority]

    # competency pillars: group by taxonomy category, extras into 'Domain Knowledge'
    groups: dict[str, list[str]] = {}
    for s in req_canon + pref_canon:
        label = CATEGORY_LABELS.get(tax.category_of(s) or "", "Core Requirements")
        groups.setdefault(label, []).append(tax.display(s))
    extras = extras_req[:6] + extras_pref[:3]
    if extras:
        groups.setdefault("Domain Knowledge", []).extend(extras)
    pillars = [
        {"area": area, "skills": names[:8], "targetScore": min(95, target + min(len(names), 4))}
        for area, names in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:5]
    ]

    # Strong signal for a known career if the title or content matches
    career = tax.match_career_title(title)
    summary = (f"{title} role requiring {', '.join(req_names[:5])}." if req_names
               else f"{title} role; no specific skills were detected in the posting text.")

    formatted = "\n\n".join(filter(None, [
        title, summary,
        ("Required Skills & Qualifications:\n- " + "\n- ".join(req_names)) if req_names else "",
        ("Preferred Qualifications:\n- " + "\n- ".join(pref_names)) if pref_names else "",
        ("Key Responsibilities:\n- " + "\n- ".join(responsibilities)) if responsibilities else "",
    ]))
    if not req_names and not pref_names:
        raise EngineError(
            "No recognisable skills were found in this posting. Paste the full requirements section or choose "
            "one of the supported technology roles (e.g. Data Analyst, Data Engineer, DevOps Engineer).",
            422, "NO_SKILLS_FOUND")
    return {
        "postingStatus": "active", "jobTitle": title, "company": "", "location": "",
        "seniority": seniority, "requiredSkills": req_names, "preferredSkills": pref_names,
        "keyResponsibilities": responsibilities, "competencyPillars": pillars,
        "formattedDescription": formatted, "sourceMethod": source,
        "matchedCareerId": career.career_id if career else None,
    }


# ------------------------------------------------------------------ URL fetching
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


def _assert_public_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise EngineError("Enter a valid http(s) job posting URL.", 400)
    host = p.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith((".local", ".internal")):
        raise EngineError("That address is not allowed.", 400)
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EngineError("The job posting address could not be resolved.", 404, "JOB_UNAVAILABLE",
                          {"postingStatus": "not_found"}) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise EngineError("That address is not allowed.", 400)


def _json_ld_job(html: str) -> dict | None:
    for m in re.finditer(r'(?is)<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            nodes = it.get("@graph", [it]) if isinstance(it, dict) else []
            for n in nodes:
                t = n.get("@type") if isinstance(n, dict) else None
                if (isinstance(t, list) and any(str(x).lower() == "jobposting" for x in t)) or str(t).lower() == "jobposting":
                    return n
    return None


def fetch_job_url(url: str, timeout: float = 9.0) -> dict:
    """Fetch a public job page with SSRF protection and return {title, company, location, text}."""
    import httpx

    url = url.strip()
    _assert_public_url(url)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; UjuzifyBot/1.0; +career-guidance)", "Accept": "text/html,application/xhtml+xml"}
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout, headers=headers) as client:
            current, resp = url, None
            for _ in range(4):
                resp = client.get(current)
                if resp.is_redirect and resp.headers.get("location"):
                    current = str(httpx.URL(current).join(resp.headers["location"]))
                    _assert_public_url(current)
                    continue
                break
    except httpx.HTTPError as exc:
        raise EngineError("The job page could not be reached. Upload the posting or paste its text instead.", 404,
                          "JOB_UNAVAILABLE", {"postingStatus": "not_found"}) from exc
    assert resp is not None
    if resp.status_code in (404, 410):
        raise EngineError("This job posting has expired or could not be found.", 410 if resp.status_code == 410 else 404,
                          "JOB_UNAVAILABLE", {"postingStatus": "expired" if resp.status_code == 410 else "not_found"})
    if resp.status_code >= 400 or len(resp.content) > 3_000_000:
        raise EngineError("The job page could not be read (blocked or too large). Paste the posting text instead.", 422,
                          "FETCH_BLOCKED")
    html = resp.text
    job = _json_ld_job(html)
    if job:
        desc = strip_html(str(job.get("description", "")))
        title = str(job.get("title", "")).strip()
        org = job.get("hiringOrganization") or {}
        loc = job.get("jobLocation") or {}
        if isinstance(loc, list) and loc:
            loc = loc[0]
        addr = (loc.get("address") if isinstance(loc, dict) else {}) or {}
        location = ", ".join(str(addr.get(k, "")) for k in ("addressLocality", "addressCountry") if isinstance(addr, dict) and addr.get(k))
        text = "\n\n".join(filter(None, [title, desc]))
        return {"title": title, "company": org.get("name", "") if isinstance(org, dict) else "", "location": location,
                "text": text[:60000], "validThrough": str(job.get("validThrough", ""))}
    text = strip_html(html)
    if len(text) < 140:
        raise EngineError("No readable job description was found at that URL. Paste the posting text instead.", 422, "FETCH_BLOCKED")
    return {"title": "", "company": "", "location": "", "text": text[:60000], "validThrough": ""}
