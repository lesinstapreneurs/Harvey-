"""The queries Scout searches with have to be searchable.

The first real prospecting pass ran three searches and found nothing, with no
error anywhere: the generated queries quoted `company_size`, so every one of
them asked the engine for a page containing the literal string
"1-50 salaries". Nothing contains that. These tests pin both the fix and the
escape hatch for a segment the generator cannot guess.
"""

from types import SimpleNamespace


from harvey.agents.scout import MAX_QUERIES_PER_CYCLE, Scout


def _bare(config) -> Scout:
    """A Scout with only what the query builder reads — no brain, no network."""
    scout = Scout.__new__(Scout)
    scout.config = config
    return scout


def build(**icp) -> list[str]:
    icp.setdefault("industries", [])
    icp.setdefault("geography", [])
    icp.setdefault("company_size", "")
    icp.setdefault("search_queries", [])
    return _bare(SimpleNamespace(icp=SimpleNamespace(**icp)))._build_company_search_queries()


def test_company_size_never_reaches_a_query():
    """It describes a company to a human; no page contains it."""
    queries = build(industries=["Organisme de formation"], geography=["France"],
                    company_size="1-50 salaries")
    assert queries
    assert all("1-50" not in q and "salaries" not in q for q in queries)


def test_no_english_scaffolding_around_a_french_segment():
    queries = build(industries=["Organisme de formation"], geography=["France"])
    joined = " ".join(queries).lower()
    assert "company" not in joined
    assert "companies" not in joined
    assert "top " not in joined


def test_the_industry_stays_quoted_and_the_geography_does_not():
    """The industry is a phrase pages use; a page saying Lyon still matches France."""
    (query,) = build(industries=["Organisme de formation"], geography=["France"])
    assert '"Organisme de formation"' in query
    assert '"France"' not in query
    assert "France" in query


def test_explicit_queries_win_over_the_guess():
    mine = ["organisme de formation reseaux sociaux Qualiopi",
            "formation community management certifiante CPF"]
    assert build(industries=["Ignored"], geography=["Ignored"],
                 search_queries=mine) == mine


def test_explicit_queries_are_capped_to_the_cycle_budget():
    many = [f"requete {i}" for i in range(10)]
    assert len(build(search_queries=many)) == MAX_QUERIES_PER_CYCLE


def test_blank_explicit_queries_fall_back_to_the_guess():
    queries = build(industries=["Organisme de formation"], search_queries=["", "   "])
    assert queries == ['"Organisme de formation"']


def test_an_empty_icp_produces_no_query_rather_than_a_junk_one():
    assert build() == []


def test_several_industries_each_get_a_query():
    queries = build(industries=["Organisme de formation", "Ecole en ligne"],
                    geography=["France"])
    assert len(queries) == 2
    assert all("France" in q for q in queries)
