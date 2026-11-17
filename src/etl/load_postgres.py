"""Load reference data and processed datasets into PostgreSQL (idempotent upserts).

Requires DATABASE_URL and `psycopg[binary]`. Order respects foreign keys.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

from src import config
from src.taxonomy import get_taxonomy

SCHEMA_SQL = config.ROOT / "database" / "schema.sql"


def _rows(p: Path):
    with open(p, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def load(database_url: str | None = None) -> dict[str, int]:
    import psycopg

    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    tax = get_taxonomy()
    counts: dict[str, int] = {}
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        for s in tax.skills.values():
            cur.execute("INSERT INTO curated.skills VALUES (%s,%s,%s,%s) ON CONFLICT (skill_id) DO UPDATE SET display_name=EXCLUDED.display_name, category=EXCLUDED.category, aliases=EXCLUDED.aliases",
                        (s.canonical_name, s.display_name, s.category, list(s.aliases)))
        for c in tax.careers.values():
            cur.execute("INSERT INTO curated.careers VALUES (%s,%s,%s,%s) ON CONFLICT (career_id) DO UPDATE SET career_name=EXCLUDED.career_name, career_family=EXCLUDED.career_family, description=EXCLUDED.description",
                        (c.career_id, c.name, c.family, c.description))
            for sk, w in c.skills.items():
                cur.execute("INSERT INTO curated.career_skills VALUES (%s,%s,%s) ON CONFLICT (career_id, skill_id) DO UPDATE SET importance=EXCLUDED.importance", (c.career_id, sk, w))
        counts["skills"], counts["careers"] = len(tax.skills), len(tax.careers)

        staging = config.PROCESSED_DIR / "jobs_staging.csv"
        if staging.exists():
            labels = {r["job_id"]: r["candidate_career_id"] or None for r in _rows(config.PROCESSED_DIR / "job_career_labels.csv")} if (config.PROCESSED_DIR / "job_career_labels.csv").exists() else {}
            n = 0
            for r in _rows(staging):
                cur.execute(
                    "INSERT INTO staging.job_postings (job_id,source_id,source_job_id,title,company_name,location_raw,country_code,description,posted_at,retrieved_at,content_hash) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULLIF(%s,'')::timestamptz,%s::timestamptz,%s) ON CONFLICT (job_id) DO NOTHING",
                    (r["job_id"], r["source_id"], r["source_job_id"], r["title"], r["company_name"], r["location_raw"], r["country_code"], r["description_raw"], r.get("posted_at", ""), r["retrieved_at"], r["content_hash"]))
                cur.execute(
                    "INSERT INTO curated.job_postings (job_id,title,company_name,city,country_code,salary_min,salary_max,candidate_career_id) "
                    "VALUES (%s,%s,%s,NULLIF(%s,''),%s,NULLIF(%s,'')::numeric,NULLIF(%s,'')::numeric,%s) ON CONFLICT (job_id) DO UPDATE SET candidate_career_id=EXCLUDED.candidate_career_id",
                    (r["job_id"], r["title"], r["company_name"], r.get("city", ""), r["country_code"], r.get("salary_min", ""), r.get("salary_max", ""), labels.get(r["job_id"])))
                n += 1
            counts["jobs"] = n
        js = config.PROCESSED_DIR / "job_skills_nlp.csv"
        if js.exists():
            n = 0
            for r in _rows(js):
                cur.execute("INSERT INTO curated.job_skills VALUES (%s,%s,%s,%s,%s) ON CONFLICT (job_id, skill_id) DO NOTHING",
                            (r["job_id"], r["skill_id"], r["matched_text"], r["context"], float(r["confidence"])))
                n += 1
            counts["job_skills"] = n
        if config.SKILL_DEMAND_CSV.exists():
            for r in _rows(config.SKILL_DEMAND_CSV):
                cur.execute("INSERT INTO analytics.skill_demand (skill_id,job_count,total_jobs,demand_rate) VALUES (%s,%s,%s,%s) "
                            "ON CONFLICT (skill_id) DO UPDATE SET job_count=EXCLUDED.job_count,total_jobs=EXCLUDED.total_jobs,demand_rate=EXCLUDED.demand_rate,calculated_at=now()",
                            (r["skill_id"], int(r["job_count"]), int(r["total_jobs"]), float(r["demand_rate"])))
            counts["skill_demand"] = len(tax.skills)
    return counts


if __name__ == "__main__":
    print(load())
