"""The French register of training organisations as a discovery source.

Free and authoritative, and the only source that carries what actually
qualifies a provider: its declaration number, whether it holds a valid
quality certification, what it declares it teaches, and how big it is.

Two of these tests exist because the real file disproved an assumption.
"""

import asyncio

import pytest

from harvey.collectors import discover as D
from harvey.collectors.discover import (
    PROVIDERS, DiscoveryQuery, ListePubliqueOF,
)

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


# ── Speciality selected by the configured category ──


def _row(**over):
    row = {
        ListePubliqueOF._NAME: "FORMASOCIAL",
        ListePubliqueOF._NDA: "11755555555",
        ListePubliqueOF._QUALITY: "true",
        ListePubliqueOF._TRAINERS: "4",
        ListePubliqueOF._TOWN: "PARIS",
        ListePubliqueOF._POSTCODE: "75011",
        ListePubliqueOF._REGION: "11",
        ListePubliqueOF._STREET: "1 rue de la Paix",
        ListePubliqueOF._SPECIALITY_CODES[0]: "320",
        ListePubliqueOF._SPECIALITY_LABELS[0]:
            "Specialites plurivalentes de la communication et de l'information",
    }
    row.update(over)
    return row


def test_the_configured_category_selects_the_speciality():
    """The NSF code is the register's vocabulary. A readable industry name
    never appears in an NSF label, so without the category the query that
    should return 1,331 organisations returns none."""
    provider = ListePubliqueOF()
    row = _row()

    # What the industry name alone does: nothing.
    assert provider._matched_speciality(row, "Organisme de formation") is None
    # What the configured code does.
    assert provider._matched_speciality(row, "320")[0] == "320"


def test_fetch_prefers_the_category_over_the_term(tmp_path, monkeypatch):
    import csv as _csv

    path = tmp_path / "register.csv"
    row = _row()
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = _csv.DictWriter(handle, fieldnames=list(row), delimiter=";")
        writer.writeheader()
        writer.writerow(row)

    provider = ListePubliqueOF()
    monkeypatch.setattr(provider, "_ensure_cached",
                        lambda client: _done(path))

    # The industry name would match nothing; the category carries the code.
    query = D.DiscoveryQuery(term="Organisme de formation", category="320",
                             limit=10)
    found = asyncio.run(provider.fetch(None, {}, query))
    assert [b.name for b in found] == ["FORMASOCIAL"]
    assert found[0].external_id == "nda:11755555555"

    # Without the category, the same row is not selected.
    bare = D.DiscoveryQuery(term="Organisme de formation", limit=10)
    assert asyncio.run(provider.fetch(None, {}, bare)) == []


async def _done(value):
    return value


def test_the_register_declares_that_it_needs_a_category():
    assert ListePubliqueOF.needs_category is True


# ── A nationwide geography is not a town ──


@pytest.mark.parametrize("location", ["France", "france", " FR ", "toute la France"])
def test_naming_the_country_filters_nothing(location):
    """``icp.geography: [France]`` is the natural way to say "everywhere".
    Filtered as a town it matched nothing, so the whole segment came back
    empty."""
    provider = ListePubliqueOF()
    assert provider._matches_location(_row(), location) is True


def test_a_town_is_still_filtered_exactly():
    provider = ListePubliqueOF()
    assert provider._matches_location(_row(), "paris") is True
    assert provider._matches_location(_row(), "lyon") is False


def test_roissy_en_france_is_a_town_not_the_country():
    """Matching the country as a substring would have made this one town
    the answer to every nationwide query."""
    provider = ListePubliqueOF()
    roissy = _row(**{ListePubliqueOF._TOWN: "ROISSY-EN-FRANCE",
                     ListePubliqueOF._POSTCODE: "95700"})
    assert provider._matches_location(roissy, "roissy-en-france") is True
    # And a Paris row is not dragged in by a query for that town.
    assert provider._matches_location(_row(), "roissy-en-france") is False
