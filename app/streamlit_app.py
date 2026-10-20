"""Ujuzify: career matches and skill gaps for graduates (Phase 6 Streamlit app).

Run from the project root, with the API already running:

    uvicorn api.main:app --host 127.0.0.1 --port 8000
    streamlit run app/streamlit_app.py

The API address comes from the UJUZIFY_API_URL environment variable (default
http://127.0.0.1:8000). It is deliberately not a field users can edit.
"""
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.product.client import ApiClient, ApiError, build_payload, md_escape, percent  # noqa: E402

st.set_page_config(page_title="Ujuzify", layout="wide")
client = ApiClient(os.environ.get("UJUZIFY_API_URL", "http://127.0.0.1:8000"))

st.title("Ujuzify")
st.caption("Find technology careers that fit your skills, see what to learn next, based on a sample of job postings.")

try:
    health = client.health()
    skill_list = client.skills()
    career_list = client.careers()
except ApiError as e:
    st.error(e.message)
    st.stop()

st.info(health["disclaimer"])
if health["synthetic_postings"]:
    st.warning(health["data_notice"])

display_to_name = {s["display_name"]: s["display_name"] for s in skill_list}
career_by_label = {"Best match (default)": None, **{c["career_name"]: c["career_id"] for c in career_list}}

with st.form("profile"):
    st.subheader("Your skills")
    chosen = st.multiselect("Skills you already have", options=list(display_to_name), key="skills_select")
    extra = st.text_input("Other skills, separated by commas", key="extra_skills",
                          help="We match common names such as 'MS Excel' or 'Postgres'.")
    target_label = st.selectbox("Career to plan for (optional)", options=list(career_by_label), key="target_select")
    free_only = st.checkbox("Free learning resources only", key="free_only")
    submitted = st.form_submit_button("Get recommendations")

if submitted:
    try:
        payload = build_payload(chosen, extra, career_by_label[target_label], free_only)
        st.session_state["result"] = client.recommend(payload)
        st.session_state.pop("error", None)
    except ValueError as e:
        st.session_state.pop("result", None)
        st.session_state["error"] = str(e)
    except ApiError as e:
        st.session_state.pop("result", None)
        st.session_state["error"] = e.message

if st.session_state.get("error"):
    st.warning(st.session_state["error"])

result = st.session_state.get("result")
if result:
    if result["unrecognized_skills"]:
        st.warning("We did not recognise these, so they were left out: "
                   + ", ".join(md_escape(s) for s in result["unrecognized_skills"]))
    st.caption("Skills used: " + ", ".join(result["recognized_skills"]))

    st.subheader("Career matches")
    for m in result["recommendations"][: result["top_n"]]:
        with st.container(border=True):
            st.markdown(f"**{m['rank']}. {m['career_name']}** — {percent(m['score'])} compatibility")
            st.progress(min(max(m["score"], 0.0), 1.0))
            notes = [f"Based on {m['postings']} sampled postings"]
            if m["low_sample"]:
                notes.append("small sample, treat as a rough guide")
            if m["tied_with"]:
                notes.append(f"tied with {m['tied_with']} other career(s); their order is arbitrary")
            st.caption("; ".join(notes))
            with st.expander("Why this match"):
                if m["matched"]:
                    st.markdown("**Skills you have that this career asks for**")
                    for e in m["matched"][:6]:
                        st.markdown(f"- {e['display_name']} — in {percent(e['frequency'])} of sampled postings")
                if m["missing"]:
                    st.markdown("**Skills it asks for that you could add**")
                    for e in m["missing"][:6]:
                        st.markdown(f"- {e['display_name']} — in {percent(e['frequency'])} of sampled postings")

    gap = result["gap"]
    st.subheader(f"What to learn next for {gap['career_name']}")
    st.caption("Chosen by you." if result["target_source"] == "requested" else "Your best-matching career.")
    if not gap["missing"]:
        st.success("No gaps against this career's sampled requirements.")
    for g in gap["missing"][:8]:
        with st.container(border=True):
            st.markdown(f"**{g['priority']}. {g['display_name']}**")
            st.caption(g["reason"])
            res = g["resource"]
            if res:
                st.markdown(f"Learn: [{res['title']}]({res['url']}) — {res['provider']} "
                            f"({res['cost']}, {res['difficulty']})")
                for alt in g["alternatives"]:
                    st.markdown(f"Also: [{alt['title']}]({alt['url']}) — {alt['provider']} ({alt['cost']})")
            else:
                st.markdown(g["resource_note"])

    st.divider()
    st.caption(result["disclaimer"])
    st.caption(result["data_limits"])
    st.caption(f"Model {result['model_version']}, generated {result['created_at']}.")
