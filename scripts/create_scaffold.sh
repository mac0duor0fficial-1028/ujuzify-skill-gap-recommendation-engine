#!/usr/bin/env bash
# Recreates the empty Phase 0 directory tree (idempotent). Run from the project root.
set -euo pipefail

KEEP_DIRS=(
  data/raw data/interim data/processed data/feedback
  data/reference data/evaluation data/graduate_profiles
  models powerbi tests
  airflow/dags database api app monitoring .github/workflows
)
OTHER_DIRS=(data/external docs scripts notebooks)
SRC_PKGS=(ingestion preprocessing nlp features models recommendation evaluation etl product)

for d in "${KEEP_DIRS[@]}"; do mkdir -p "$d"; touch "$d/.gitkeep"; done
for d in "${OTHER_DIRS[@]}"; do mkdir -p "$d"; done
touch src/__init__.py 2>/dev/null || { mkdir -p src; touch src/__init__.py; }
for p in "${SRC_PKGS[@]}"; do mkdir -p "src/$p"; touch "src/$p/__init__.py"; done
echo "Scaffold ready."
