#!/usr/bin/env bash
# Phase 6 smoke test: recommendation CLI -> API (uvicorn) -> Streamlit server.
# Run from the project root after the earlier phases have produced data/processed/career_skills.csv:
#   python src/recommendation/build_career_skills.py && bash scripts/smoke_phase6.sh
# It starts real servers on localhost, checks them, and always stops them again.
set -euo pipefail

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"
API_PID=""
UI_PID=""

cleanup() {
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$UI_PID" ]] && kill "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT

wait_for() {
  for _ in $(seq 1 80); do
    curl -fsS "$1" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "FAILED: timed out waiting for $1" >&2
  return 1
}

echo "1/4 recommendation CLI (demo profile must rank Data Analyst, CA01, first)"
python src/recommendation/recommend.py --expect-top CA01 > /dev/null
echo "    ok"

echo "2/4 API on 127.0.0.1:${API_PORT}"
python -m uvicorn api.main:app --host 127.0.0.1 --port "$API_PORT" --log-level warning &
API_PID=$!
wait_for "http://127.0.0.1:${API_PORT}/health"
TOP=$(curl -fsS -X POST "http://127.0.0.1:${API_PORT}/recommend" \
  -H 'Content-Type: application/json' \
  -d '{"skills": ["Excel", "SQL", "Statistics", "Data Visualization", "Python", "Communication"]}' |
  python -c 'import json,sys; r=json.load(sys.stdin); print(r["recommendations"][0]["career_id"], r["model_version"])')
echo "    POST /recommend -> top career and model: ${TOP}"
[[ "${TOP%% *}" == "CA01" ]] || { echo "FAILED: expected CA01 first" >&2; exit 1; }

echo "3/4 Streamlit on 127.0.0.1:${UI_PORT}"
UJUZIFY_API_URL="http://127.0.0.1:${API_PORT}" python -m streamlit run app/streamlit_app.py \
  --server.headless true --server.address 127.0.0.1 --server.port "$UI_PORT" \
  --browser.gatherUsageStats false >/dev/null 2>&1 &
UI_PID=$!
wait_for "http://127.0.0.1:${UI_PORT}/_stcore/health"
curl -fsS "http://127.0.0.1:${UI_PORT}/" | grep -qi "<title>" || { echo "FAILED: no page served" >&2; exit 1; }
echo "    health ok, page served"

echo "4/4 done: servers stopped on exit"
echo "SMOKE TEST PASSED"
