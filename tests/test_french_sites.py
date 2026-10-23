"""Reading a French company's site, and not mistaking a directory for one.

The first real prospecting pass on a French segment produced twenty 404s per
company and not one named person. Two reasons, both visible in the log: the
paths probed were English only, and three of the ten "companies" found were a
directory, a comparison site and an HR magazine — each scraped in full before
anyone noticed.
"""

from harvey.agents.scout import INBOX_PAGES, Scout


def scout() -> Scout:
    bare = Scout.__new__(Scout)
    bare._seen_domains = set()
    return bare


def team_paths() -> list[str]:
    import inspect
    source = inspect.getsource(Scout._scrape_team_page)
    start = source.index("team_paths = [")
    end = source.index("]", start)
    return [p.strip().strip('"') for p in source[start + 14:end].split(",") if p.strip()]


# ── French paths ───────────────────────────────────────────────────────

def test_mentions_legales_is_probed():
    """French law puts the director's name there. It is the best single page."""
    assert "/mentions-legales" in team_paths()


def test_french_team_pages_are_probed():
    paths = team_paths()
    for path in ("/equipe", "/notre-equipe", "/a-propos", "/qui-sommes-nous"):
        assert path in paths, path


def test_english_team_pages_are_kept():
    """The fix adds; it does not take away from an English segment."""
    paths = team_paths()
    for path in ("/team", "/about", "/our-team", "/staff"):
        assert path in paths, path


def test_french_career_pages_are_probed():
    for path in ("/recrutement", "/nous-rejoindre", "/carrieres"):
        assert path in Scout._CAREERS_PATHS, path


def test_french_contact_pages_are_probed():
    for path in ("/nous-contacter", "/contactez-nous"):
        assert path in INBOX_PAGES, path


# ── directories, comparison sites and magazines ────────────────────────

def test_the_three_sites_the_first_run_scraped_are_now_refused():
    for domain in ("iciformation.fr", "learnthings.fr", "rhmatin.com"):
        assert scout()._is_noise_domain(domain), domain


def test_a_directory_is_recognised_by_its_shape():
    """A new one appears every month; enumerating them is a losing game."""
    for domain in ("annuaire-formation.fr", "comparateur-formation.com",
                   "meilleurformation.fr", "classement-ecoles.fr",
                   "guide-formation.net", "blog.exemple.fr"):
        assert scout()._is_noise_domain(domain), domain


def test_a_real_training_organisation_is_kept():
    """The filter must not swallow the prospects it exists to protect."""
    for domain in ("kassiopee-formation.com", "formavance.fr", "etid.fr",
                   "prismeaformation.fr", "cygnus-formations.fr"):
        assert not scout()._is_noise_domain(domain), domain


def test_public_registries_are_not_prospects():
    for domain in ("francecompetences.fr", "moncompteformation.gouv.fr",
                   "pole-emploi.fr", "pappers.fr"):
        assert scout()._is_noise_domain(domain), domain


def test_a_subdomain_of_noise_is_noise():
    assert scout()._is_noise_domain("fr.linkedin.com")
