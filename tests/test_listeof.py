"""The French register of training organisations as a discovery source.

Free and authoritative, and the only source that carries what actually
qualifies a provider: its declaration number, whether it holds a valid
quality certification, what it declares it teaches, and how big it is.

Two of these tests exist because the real file disproved an assumption.
"""

import asyncio

import pytest

from harvey.collectors.discover import PROVIDERS, DiscoveryQuery

HEADER = ";".join([
    "numeroDeclarationActivite", "denomination",
    "adressePhysiqueOrganismeFormation.voie",
    "adressePhysiqueOrganismeFormation.codePostal",
    "adressePhysiqueOrganismeFormation.ville",
    "adressePhysiqueOrganismeFormation.codeRegion",
    "certifications.actionsDeFormation",
    "informationsDeclarees.specialitesDeFormation.codeSpecialite1",
    "informationsDeclarees.specialitesDeFormation.libelleSpecialite1",
    "informationsDeclarees.specialitesDeFormation.codeSpecialite2",
    "informationsDeclarees.specialitesDeFormation.libelleSpecialite2",
    "informationsDeclarees.specialitesDeFormation.codeSpecialite3",
    "informationsDeclarees.specialitesDeFormation.libelleSpecialite3",
    "informationsDeclarees.effectifFormateurs",
])

ROWS = [
    # Paris, certified, declares 320 first. The straightforward match.
    '"11757525575";"ACTIV ACADEMIE";"1 RUE A";"75008";"PARIS 08";"11";true;'
    '"320";"Specialites plurivalentes de la communication";"";"";"";"";3',
    # Bergerac, region code 75 (Nouvelle-Aquitaine) — the collision.
    '"75240211724";"ACQUIFORMATIONS";"2 RUE B";"24100";"BERGERAC";"75";true;'
    '"320";"Specialites plurivalentes de la communication";"";"";"";"";2',
    # Declares 321 second, and something else first.
    '"91340849434";"21 INTERACTIVE";"3 RUE C";"34000";"MONTPELLIER";"76";true;'
    '"312";"Commerce, vente";"321";"Journalisme et communication";"";"";1',
    # Right speciality, no quality certification.
    '"11111111111";"SANS QUALIOPI";"4 RUE D";"75009";"PARIS 09";"11";false;'
    '"320";"Specialites plurivalentes de la communication";"";"";"";"";2',
    # Right speciality and certified, but a national group.
    '"22222222222";"GROS GROUPE";"5 RUE E";"75010";"PARIS 10";"11";true;'
    '"320";"Specialites plurivalentes de la communication";"";"";"";"";450',
]


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """The real provider, pointed at a small file instead of the 36 MB one."""
    source = tmp_path / "liste.csv"
    source.write_text("\n".join([HEADER, *ROWS]) + "\n", encoding="utf-8")
    one = PROVIDERS["listeof"]

    async def cached(_client):
        return source

    monkeypatch.setattr(one, "_ensure_cached", cached)
    return one


def found(provider, term="", location="", limit=50) -> list:
    return asyncio.run(
        provider.fetch(None, {}, DiscoveryQuery(term=term, location=location, limit=limit)))


def names(provider, **kw) -> list[str]:
    return [b.name for b in found(provider, **kw)]


def test_a_bare_number_is_a_departement_not_a_region_code(provider):
    """75 is Paris, and also the region code of Nouvelle-Aquitaine.

    Matching a bare number against both answered a query for Paris with a
    provider in Bergerac. The real file surfaced this; it is not theoretical.
    """
    assert "ACQUIFORMATIONS" not in names(provider, term="320", location="75")
    assert "ACTIV ACADEMIE" in names(provider, term="320", location="75")


def test_a_region_is_asked_for_explicitly(provider):
    assert names(provider, term="320", location="region:75") == ["ACQUIFORMATIONS"]


def test_a_town_matches_by_name(provider):
    assert "ACTIV ACADEMIE" in names(provider, term="320", location="paris")
    assert "ACQUIFORMATIONS" not in names(provider, term="320", location="paris")


def test_the_reported_speciality_is_the_one_that_matched(provider):
    """Reporting the first declared speciality reads as a targeting bug."""
    (business,) = [b for b in found(provider, term="321")]
    assert business.name == "21 INTERACTIVE"
    assert business.category == "Journalisme et communication"


def test_a_speciality_matches_by_label_too(provider):
    assert "21 INTERACTIVE" in names(provider, term="journalisme")


def test_an_uncertified_provider_is_refused(provider):
    """No quality certification means no CPF and no OPCO funding."""
    assert "SANS QUALIOPI" not in names(provider, term="320")


def test_a_national_group_is_refused_by_the_size_cap(provider):
    assert "GROS GROUPE" not in names(provider, term="320")


def test_the_size_cap_can_be_lifted(provider, monkeypatch):
    monkeypatch.setattr(provider, "MAX_TRAINERS", 0)
    assert "GROS GROUPE" in names(provider, term="320")


def test_identity_is_the_declaration_number(provider):
    """An exclusion list keyed on the same NDA has to resolve to the same
    company, which it cannot if each side invents its own prefix."""
    for business in found(provider, term="320"):
        assert business.external_id.startswith("nda:")
    assert "nda:11757525575" in {b.external_id for b in found(provider, term="320")}


def test_the_register_carries_no_website(provider):
    """Stated, not assumed: contacts need a separate resolution pass."""
    assert all(not b.domain for b in found(provider, term="320"))


def test_the_source_is_free(provider):
    assert provider.estimate([DiscoveryQuery(term="320")]) == 0.0
    assert provider.cost_note == "free"
