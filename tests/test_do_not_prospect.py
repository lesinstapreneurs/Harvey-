"""A client or current partner must never be prospected.

Marking them excluded in a cohort query is not enough. The contacts backfill
does not go through cohort() — it reads companies_needing_prospects() — so
without a filter there, Harvey goes looking for the decision-maker at a
company that already signed. These tests pin both halves: the marker filters
the backfill, and entity resolution stops discovery recreating the row.
"""

import pytest
import pytest_asyncio

from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.state import DO_NOT_PROSPECT, StateManager


@pytest_asyncio.fixture
async def state(tmp_path):
    state = StateManager(str(tmp_path / "exclusions.db"))
    await state.init_db()
    await state.upsert_signal_code(
        DO_NOT_PROSPECT, label="Do not prospect",
        description="An existing client or partner.", status="confirmed",
    )
    return state


async def mark(state, company_id, why="existing partner"):
    await state.add_observation(
        DO_NOT_PROSPECT, company_id=company_id, collector="import", value_text=why,
    )


@pytest.mark.asyncio
async def test_backfill_skips_a_marked_company(state):
    free = await state.add_company(Company(name="Free Ltd", domain="free.example"))
    partner = await state.add_company(Company(name="Partner SA", domain="partner.example"))
    assert {c["id"] for c in await state.companies_needing_prospects()} == {free, partner}

    await mark(state, partner)
    assert [c["id"] for c in await state.companies_needing_prospects()] == [free]


@pytest.mark.asyncio
async def test_the_marker_survives_more_companies_arriving(state):
    partner = await state.add_company(Company(name="Partner SA", domain="partner.example"))
    await mark(state, partner)
    for i in range(5):
        await state.add_company(Company(name=f"New {i}", domain=f"new{i}.example"))
    ids = {c["id"] for c in await state.companies_needing_prospects(limit=50)}
    assert partner not in ids
    assert len(ids) == 5


@pytest.mark.asyncio
async def test_a_bare_domain_dedups_against_itself(state):
    """Identity is the domain, so the same host does not come back twice."""
    partner = await state.add_company(Company(name="Partner SA", domain="partner.example"))
    await mark(state, partner)
    again = await state.add_company(Company(name="PARTNER S.A.", domain="  Partner.Example  "))
    assert again == partner


@pytest.mark.asyncio
async def test_a_url_and_a_bare_domain_are_still_two_companies(state):
    """A known gap, pinned so a fix is a deliberate change, not a surprise.

    `_norm` only strips and lowercases — it does not remove the scheme, `www.`
    or the path, and it is shared with email normalisation, so widening it is
    not a local change. Anything feeding Harvey a site column (an import, a
    directory) must therefore reduce it to a bare host first, or the same
    company lands twice and an exclusion marked on one row misses the other.
    """
    bare = await state.add_company(Company(name="Partner SA", domain="partner.example"))
    url = await state.add_company(Company(name="Partner SA", domain="https://www.partner.example/"))
    assert bare != url


@pytest.mark.asyncio
async def test_a_company_without_the_marker_is_still_backfilled(state):
    """The filter must not quietly swallow everything."""
    free = await state.add_company(Company(name="Free Ltd", domain="free.example"))
    assert [c["id"] for c in await state.companies_needing_prospects()] == [free]


@pytest.mark.asyncio
async def test_a_marked_company_with_contacts_is_unaffected(state):
    """The backfill only ever looked at companies with no prospects."""
    partner = await state.add_company(Company(name="Partner SA", domain="partner.example"))
    await state.add_prospect(Prospect(
        company_id=partner, first_name="Jo", last_name="Day", title="CEO",
        email="jo@partner.example", email_status="verified",
    ))
    await mark(state, partner)
    assert await state.companies_needing_prospects() == []


@pytest.mark.asyncio
async def test_cohort_excludes_the_marker_too(state):
    """Both paths honour it: the cohort query and the backfill."""
    partner = await state.add_company(Company(name="Partner SA", domain="partner.example"))
    await mark(state, partner)
    assert await state.cohort(require=[DO_NOT_PROSPECT]) == [partner]
    assert await state.cohort(require=[DO_NOT_PROSPECT], exclude=[DO_NOT_PROSPECT]) == []


@pytest.mark.asyncio
async def test_discovery_runs_before_profile_search(state):
    """Profile search spends the whole query budget on LinkedIn, which blocks
    scrapers. Running it first starved discovery and the funnel got no input."""
    from types import SimpleNamespace

    from harvey.agents.scout import Scout
    from harvey.config import EnvConfig, HarveyConfig

    import harvey.agents.scout as scout_module

    ordered = []

    class Recorder(Scout):
        async def _prospect_via_hunter(self):
            return 0

        async def _prospect_via_known_companies(self):
            return 0

        async def _prospect_via_hiring_boards(self):
            return 0

        async def _prospect_via_company_discovery(self):
            ordered.append("company_discovery")
            return 0

        async def _prospect_via_profile_search(self):
            ordered.append("profile_search")
            return 0

    cfg = HarveyConfig.model_validate(_minimal_config())
    brain = SimpleNamespace(load_skills_for_agent=lambda *_: "")
    await Recorder(brain, state, cfg, EnvConfig()).run()
    assert ordered == ["company_discovery", "profile_search"]


def _minimal_config() -> dict:
    import yaml

    from harvey.paths import PROJECT_ROOT

    return yaml.safe_load((PROJECT_ROOT / "harvey.yaml").read_text(encoding="utf-8"))
