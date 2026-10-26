"""Choosing a website for an organisation the register named but did not link.

One rule shapes every case here: a wrong domain is worse than no domain.
Attaching the wrong site means reading the wrong company's pages, inferring
the wrong email pattern, and eventually sending mail to a stranger in a
prospect's name. So anything ambiguous must come back as "no answer".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from resolve_domains import (  # noqa: E402
    best_domain, coverage, fold, significant_tokens,
)


def test_accents_and_punctuation_are_folded():
    assert fold("Académie Créa-Pulse") == "academie crea pulse"


def test_legal_forms_and_filler_are_not_identity():
    assert significant_tokens("CENTRE DE FORMATION SARL") == []
    assert significant_tokens("KASSIOPEE FORMATION") == ["kassiopee"]


def test_formation_is_filler_in_this_register():
    """Nearly every name contains it, so it matches everything or nothing."""
    assert "formation" not in significant_tokens("PRISMEA FORMATION")


def test_coverage_counts_identifying_tokens_only():
    assert coverage("KASSIOPEE FORMATION", "kassiopee-formation.com") == 1.0
    assert coverage("KASSIOPEE FORMATION", "unrelated.fr") == 0.0


def test_a_name_made_only_of_filler_matches_nothing():
    """"CENTRE DE FORMATION" identifies no one; matching it would be a lie."""
    assert best_domain("CENTRE DE FORMATION", ["centre-formation.fr"]) is None


def test_the_obvious_match_is_taken():
    found = best_domain("KASSIOPEE FORMATION",
                        ["annuaire.fr", "kassiopee-formation.com", "autre.fr"])
    assert found == ("kassiopee-formation.com", 1.0)


def test_a_two_token_name_needs_half_its_tokens():
    assert best_domain("PRISMEA TALIORA", ["prismeaformation.fr"]) is not None
    assert best_domain("PRISMEA TALIORA", ["quelquechose.fr"]) is None


def test_two_equally_good_candidates_are_an_ambiguity_not_a_pick():
    """A franchise and its head office, or two namesakes. Refuse both."""
    assert best_domain("ORYKA PROGRAM", ["oryka.fr", "oryka.com"]) is None


def test_one_identifying_token_matched_twice_is_still_ambiguous():
    """CYGNUS FORMATIONS identifies on "cygnus" alone — "formations" is filler.

    Both candidates carry it equally, so there is no evidence to choose
    between them, and cygnusandmore-holding.fr may be a different company
    entirely. Refusing is the correct answer, not a shortcoming.
    """
    assert best_domain("CYGNUS FORMATIONS",
                       ["cygnus-formations.fr", "cygnusandmore-holding.fr"]) is None


def test_a_clear_winner_beats_a_partial_match():
    """Two identifying tokens: one candidate carries both, the other one."""
    found = best_domain("ORYKA PROGRAM",
                        ["orykaprogram.fr", "oryka-autrechose.fr"])
    assert found is not None and found[0] == "orykaprogram.fr"
    assert found[1] == 1.0


def test_a_refused_domain_is_never_picked():
    """Directories rank well for exactly these names."""
    refused = {"annuaire-formation.fr"}
    assert best_domain("ANNUAIRE FORMATION", ["annuaire-formation.fr"],
                       refuse=refused.__contains__) is None


def test_junk_candidates_are_ignored():
    assert best_domain("KASSIOPEE", ["", "not-a-domain", "kassiopee.fr"]) \
        == ("kassiopee.fr", 1.0)


def test_no_candidates_means_no_answer():
    assert best_domain("KASSIOPEE FORMATION", []) is None


def test_the_tld_does_not_carry_the_name():
    """Matching against the TLD would make every .fr look French-named."""
    assert best_domain("FRANCE", ["somethingelse.fr"]) is None


def test_a_subdomain_still_matches_its_organisation():
    found = best_domain("ETSA FORMATION", ["etsa.catalogueformpro.com"])
    assert found is not None and found[0] == "etsa.catalogueformpro.com"


# ── which organisations are even offered for resolution ────────────────

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from harvey.models.company import Company  # noqa: E402
from harvey.state import DO_NOT_PROSPECT, StateManager  # noqa: E402

from resolve_domains import pending  # noqa: E402


@pytest_asyncio.fixture
async def state(tmp_path):
    state = StateManager(str(tmp_path / "resolve.db"))
    await state.init_db()
    await state.upsert_signal_code(
        DO_NOT_PROSPECT, label="Do not prospect",
        description="An existing client or partner.", status="confirmed")
    return state


@pytest.mark.asyncio
async def test_excluded_partners_do_not_crowd_out_the_candidates(state):
    """The exclusion has to be in the query, not applied after the LIMIT.

    The partners are imported first, so they are the oldest rows. Fetching a
    window ordered by age and filtering it afterwards let them fill the window
    and the tool reported "nothing to resolve" with 38 organisations waiting.
    """
    for i in range(30):
        partner = await state.add_company(Company(
            name=f"PARTENAIRE {i}", domain="", external_id=f"nda:{i:011d}"))
        await state.add_observation(DO_NOT_PROSPECT, company_id=partner,
                                    collector="import", value_text="partner")
    wanted = await state.add_company(Company(
        name="NOUVEAU PROSPECT", domain="", external_id="nda:99999999999"))

    todo = await pending(state, 5)
    assert [r["id"] for r in todo] == [wanted]


@pytest.mark.asyncio
async def test_a_company_that_already_has_a_domain_is_skipped(state):
    await state.add_company(Company(name="DEJA RESOLU", domain="deja.fr",
                                    external_id="nda:1"))
    assert await pending(state, 5) == []


@pytest.mark.asyncio
async def test_the_limit_is_respected(state):
    for i in range(10):
        await state.add_company(Company(name=f"OF {i}", domain="",
                                        external_id=f"nda:{i}"))
    assert len(await pending(state, 3)) == 3
