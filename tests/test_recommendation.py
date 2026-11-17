import pytest

from src.product.feedback_store import record_feedback
from src.product.personalization import build_recommendation
from src.product.resource_recommender import load_projects, load_resources, rank_projects, resources_for_skill
from src.recommendation.recommendation_engine import Engine
from src.taxonomy import get_taxonomy

DEMO = ["Python", "SQL", "Excel", "Power BI", "Git"]


def test_demo_profile_ranks_data_analyst_first():
    tax = get_taxonomy()
    owned, _ = tax.parse_skill_list(DEMO)
    ranked = Engine(tax).rank_careers(owned)
    assert ranked[0].career_name == "Data Analyst" and ranked[1].career_name == "Business Intelligence Analyst"
    assert ranked[0].match_score <= 1 and "Statistics" in [tax.display(s) for s in ranked[0].missing_skills]


def test_scoring_formula_with_and_without_demand():
    tax = get_taxonomy()
    e = Engine(tax)
    assert e.match_score(0.6, None) == 0.6
    e2 = Engine(tax, {"sql": 0.5})
    assert e2.match_score(0.6, 1.0) == pytest.approx(0.85 * 0.6 + 0.15 * 1.0)
    assert e2.market_adjustment({"sql": 1.0}) == 1.0  # min(1, .5*5)
    assert e.gap_priority(0.8, 0.2) == pytest.approx(0.8 * (0.75 + 0.5 * 0.2))


def test_gaps_exclude_owned_and_unknown_career_fails():
    tax = get_taxonomy()
    e = Engine(tax)
    gaps = e.skill_gaps("CA01", ["sql", "excel"])
    assert "sql" not in [g.skill_id for g in gaps]
    assert gaps == sorted(gaps, key=lambda g: (-g.priority, g.skill_id))
    with pytest.raises(KeyError):
        e.skill_gaps("CA99", [])


def test_parse_skill_list_formats_and_unknowns():
    owned, unknown = get_taxonomy().parse_skill_list("Python|SQL; Excel, Underwater Basket Weaving")
    assert owned == ["python", "sql", "excel"] and unknown == ["Underwater Basket Weaving"]


def test_catalogues_cover_every_skill_and_projects_valid():
    tax = get_taxonomy()
    have = {r.skill_id for r in load_resources()}
    assert set(tax.skills) <= have
    for p in load_projects():
        assert set(p.skills) <= set(tax.skills)
    assert resources_for_skill("docker")[0].cost_tier == "free"


def test_projects_prefer_career_and_overlap():
    top = rank_projects("CA03", ["airflow", "etl"])[0][0]
    assert "CA03" in top.career_ids


def test_build_recommendation_payload():
    r = build_recommendation({"profile_id": "x", "skills": DEMO})
    assert r["target_career"]["career_name"] == "Data Analyst"
    assert r["learning_resources"] and r["portfolio_projects"] and "not probabilities" in r["disclaimer"]
    with pytest.raises(KeyError):
        build_recommendation({"profile_id": "x", "skills": DEMO}, "CA77")


def test_feedback_store(tmp_path):
    p = tmp_path / "fb.csv"
    record_feedback("p1", "CA01", "useful", "great\nthanks", path=p)
    record_feedback("p1", "CA01", "rejected", path=p)
    assert len(p.read_text().strip().splitlines()) == 3
    with pytest.raises(ValueError):
        record_feedback("p1", "CA01", "meh", path=p)
