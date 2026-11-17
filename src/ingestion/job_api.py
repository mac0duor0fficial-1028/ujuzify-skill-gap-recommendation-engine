"""API-first job acquisition into the canonical raw-job schema (project report, Section 2.2-2.4).

Raw payloads are stored immutably as JSONL; normalised records go to jobs_normalized.csv.
No scraping and no access-control bypass. Verify endpoint parameters against the source's current docs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src import config

SCHEMA_FIELDS = [
    "source_id", "source_job_id", "source_url", "title", "company_name", "location_raw", "country_code", "city",
    "description_raw", "employment_type", "seniority", "education_required", "experience_required",
    "salary_min", "salary_max", "currency", "posted_at", "retrieved_at", "source_category", "raw_payload_path",
    "content_hash",
]


def load_config(path: Path | None = None) -> dict:
    with open(path or config.EXTERNAL_DIR / "acquisition_config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def content_hash(title: str, company: str, description: str) -> str:
    norm = re.sub(r"\s+", " ", f"{title}|{company}|{description}".lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _pick(d: dict, *keys: str, default: Any = "") -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _num(v: Any) -> str:
    try:
        return str(float(v))
    except (TypeError, ValueError):
        return ""


def normalize_record(raw: dict, source_id: str, retrieved_at: str, payload_path: str, country: str = "KE") -> dict | None:
    """Map one source record to the canonical schema; returns None when required fields are absent."""
    jid = _pick(raw, "id", "job_id", "uuid", "slug")
    title = str(_pick(raw, "title", "job_title", "name")).strip()
    if not jid or not title:
        return None
    company = _pick(raw, "company", "company_name", "employer", "organization")
    if isinstance(company, dict):
        company = company.get("name", "")
    loc = _pick(raw, "location", "location_raw", "city")
    if isinstance(loc, dict):
        loc = ", ".join(str(v) for v in loc.values() if v)
    desc = str(_pick(raw, "description", "description_raw", "snippet", "summary", "content"))
    cc = str(_pick(raw, "country", "country_code", default="")).upper()[:2]
    row = {
        "source_id": source_id, "source_job_id": str(jid), "source_url": _pick(raw, "url", "apply_url", "link"),
        "title": title, "company_name": str(company).strip(), "location_raw": str(loc).strip(),
        "country_code": cc or "", "city": _pick(raw, "city") if isinstance(_pick(raw, "city"), str) else "",
        "description_raw": desc, "employment_type": _pick(raw, "employment_type", "job_type"),
        "seniority": _pick(raw, "seniority", "experience_level"), "education_required": _pick(raw, "education"),
        "experience_required": _pick(raw, "experience"), "salary_min": _num(_pick(raw, "salary_min")),
        "salary_max": _num(_pick(raw, "salary_max")), "currency": _pick(raw, "currency", "salary_currency"),
        "posted_at": _pick(raw, "posted_at", "date_posted", "published_at", "created_at"),
        "retrieved_at": retrieved_at, "source_category": _pick(raw, "category", "source_category"),
        "raw_payload_path": payload_path, "content_hash": content_hash(title, str(company), desc),
    }
    return row


def _extract_items(payload: Any, keys: list[str]) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in keys:
            if isinstance(payload.get(k), list):
                return [x for x in payload[k] if isinstance(x, dict)]
    return []


def fetch_jobs(cfg: dict | None = None, client=None) -> list[dict]:
    """Page through the configured endpoint and return raw records."""
    import httpx

    cfg = (cfg or load_config())["primary_source"]
    own = client is None
    client = client or httpx.Client(timeout=cfg.get("timeout_seconds", 20), headers={"Accept": "application/json"})
    out: list[dict] = []
    try:
        for page in range(1, int(cfg.get("max_pages", 10)) + 1):
            r = client.get(cfg["base_url"].rstrip("/") + cfg["endpoint"],
                           params={"country": cfg["country"], cfg["page_param"]: page, cfg["limit_param"]: cfg["page_size"]})
            r.raise_for_status()
            items = _extract_items(r.json(), cfg.get("results_keys", ["data"]))
            if not items:
                break
            out.extend(items)
            if len(items) < int(cfg["page_size"]):
                break
    finally:
        if own:
            client.close()
    return out


def run(cfg_path: Path | None = None, raw_dir: Path | None = None, client=None) -> tuple[Path, Path, int]:
    cfg = load_config(cfg_path)
    src = cfg["primary_source"]
    raw_dir = Path(raw_dir or config.RAW_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = fetch_jobs(cfg, client)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw_path = raw_dir / "jobs_joa_raw.jsonl"
    with open(raw_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps({"retrieved_at": ts, "source_id": src["source_id"], "payload": rec}, ensure_ascii=False) + "\n")
    rows = [r for rec in records if (r := normalize_record(rec, src["source_id"], ts, str(raw_path), src["country"]))]
    norm_path = raw_dir / "jobs_normalized.csv"
    with open(norm_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return raw_path, norm_path, len(rows)


if __name__ == "__main__":
    rp, npth, n = run()
    print(f"retrieved {n} normalised records\n  raw: {rp}\n  normalised: {npth}")
