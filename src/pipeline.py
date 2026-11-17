"""Run the full batch pipeline: ingest -> validate -> transform -> NLP -> demand -> labels [-> PostgreSQL].

Usage: python -m src.pipeline [--skip-ingest] [--load-db]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from src.etl import calculate_skill_demand, label_careers, transform_jobs
from src.ingestion import job_api
from src.nlp import extract_job_skills
from src.preprocessing import validate_jobs


def run(skip_ingest: bool = False, load_db: bool = False) -> dict:
    out: dict = {}
    if not skip_ingest:
        _, _, n = job_api.run()
        out["ingested"] = n
    rep = validate_jobs.validate()
    out["validation"] = rep
    if not rep["ok"]:
        raise SystemExit(f"validation failed: {rep['errors']}")
    out["transform"] = transform_jobs.transform()
    if out["transform"]["output"] == 0:
        raise SystemExit("no Kenya job postings after cleaning; refusing to compute demand")
    out["skill_relationships"] = extract_job_skills.run()
    out["jobs_for_demand"] = calculate_skill_demand.calculate()
    out["career_labels"] = label_careers.label()
    if load_db:
        from src.etl.load_postgres import load
        out["database"] = load(os.getenv("DATABASE_URL"))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ingest", action="store_true", help="reuse data/raw/jobs_normalized.csv")
    ap.add_argument("--load-db", action="store_true", help="load results into PostgreSQL (DATABASE_URL)")
    a = ap.parse_args()
    print(json.dumps(run(a.skip_ingest, a.load_db), indent=2, default=str))
    sys.exit(0)
