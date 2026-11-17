import pytest

from src.nlp.hybrid_skill_extractor import SkillExtractor
from src.taxonomy import get_taxonomy, load_taxonomy, normalize_text


def test_taxonomy_size_and_integrity():
    t = get_taxonomy()
    assert len(t.skills) == 70 and len(t.careers) == 9
    for c in t.careers.values():
        assert c.skills and all(s in t.skills for s in c.skills)
    for s in t.skills.values():
        assert all(r in t.skills for r in s.related)


def test_normalisation_collapses_whitespace_not_removes():
    assert normalize_text("Senior   Data\tEngineer") == "senior data engineer"
    assert normalize_text("CI/CD & Node.js") == "ci cd and node.js"


@pytest.mark.parametrize("raw,canon", [("MS Excel", "excel"), ("Postgres", "postgresql"), ("Power BI", "power_bi"),
                                       ("Amazon Web Services", "aws"), ("C#", "csharp"), ("CI/CD", "ci_cd"), ("k8s", "kubernetes")])
def test_alias_resolution(raw, canon):
    assert get_taxonomy().resolve(raw) == canon


def test_ambiguous_short_terms_are_not_matched():
    assert SkillExtractor(backend="regex").skills("Let's go and run r over the data") == []


def test_overlap_resolution_and_single_relationship():
    ex = SkillExtractor(backend="regex")
    ms = ex.extract("Python programming and Python again; python3 too")
    assert [m.canonical_name for m in ms] == ["python"]
    assert ms[0].matched_text == "python programming"


def test_evidence_and_confidence():
    m = SkillExtractor(backend="regex").extract("Experience with Postgres and Power BI required")
    by = {x.canonical_name: x for x in m}
    assert by["postgresql"].confidence < by["power_bi"].confidence  # alias < exact
    assert "postgres" in by["postgresql"].context and by["postgresql"].extraction_method.startswith("phrase_match")


def test_empty_text_is_safe():
    assert SkillExtractor(backend="regex").extract("") == [] and SkillExtractor(backend="regex").extract(None) == []


def test_bad_taxonomy_file_raises(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        load_taxonomy(skills_csv=p)


def test_career_title_longest_keyword_wins():
    t = get_taxonomy()
    assert t.match_career_title("Senior Machine Learning Engineer").career_id == "CA05"
    assert t.match_career_title("Junior Data Analyst (Remote)").career_id == "CA01"
    assert t.match_career_title("Chef") is None


def test_spacy_backend_matches_regex_backend():
    pytest.importorskip("spacy")
    text = "Junior Data Engineer: Python programming, Postgres, Amazon Web Services, CI/CD, C#, Node.js, MS Excel, Power BI and R programming."
    a = [m.canonical_name for m in SkillExtractor(backend="spacy").extract(text)]
    b = [m.canonical_name for m in SkillExtractor(backend="regex").extract(text)]
    assert a == b and len(a) >= 8
