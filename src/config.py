"""Central configuration: filesystem layout and environment-driven settings."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("UJUZIFY_DATA_DIR", ROOT / "data"))

EXTERNAL_DIR = DATA_DIR / "external"
REFERENCE_DIR = DATA_DIR / "reference"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
EVALUATION_DIR = DATA_DIR / "evaluation"
FEEDBACK_DIR = DATA_DIR / "feedback"

SKILL_TAXONOMY_CSV = EXTERNAL_DIR / "skill_taxonomy.csv"
CAREER_TAXONOMY_CSV = EXTERNAL_DIR / "career_taxonomy.csv"
CAREER_PROFILES_CSV = REFERENCE_DIR / "career_profiles.csv"
LEARNING_RESOURCES_CSV = REFERENCE_DIR / "learning_resources.csv"
PROJECTS_CSV = REFERENCE_DIR / "project_recommendations.csv"
SKILL_DEMAND_CSV = PROCESSED_DIR / "skill_demand.csv"
FEEDBACK_CSV = FEEDBACK_DIR / "recommendation_feedback.csv"

APP_NAME = "ujuzify-skill-gap-engine"
APP_VERSION = "1.0.0"
MODEL_VERSION = "baseline-weighted-coverage-v1"

# Scoring constants (documented in the project report, Section 3.8.2)
COVERAGE_WEIGHT = 0.85
MARKET_WEIGHT = 0.15
MARKET_SCALE = 5.0
PARTIAL_CREDIT = 0.5


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()
