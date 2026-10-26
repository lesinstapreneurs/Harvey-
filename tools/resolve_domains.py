"""Give the register's organisations a website, or leave them without one.

The register of training providers carries no website and no email, so its
organisations arrive identified by declaration number and nothing else. Harvey
cannot read a site it does not have, so nothing downstream works until a
domain is attached.

The rule that shapes this whole file: **a wrong domain is worse than no
domain.** Attaching the wrong site means reading the wrong company's pages,
inferring the wrong email pattern, and eventually emailing a stranger in the
name of a prospect. So a candidate is accepted only when the organisation's
own name is recognisably in the domain, and anything ambiguous is left
unresolved for a human or a later pass.

    HARVEY_HOME=~/.harvey-massena python tools/resolve_domains.py --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import unicodedata

from harvey.state import DO_NOT_PROSPECT, StateManager

# Legal forms and filler that say nothing about identity. "FORMATION" is in
# here on purpose: in this register almost every name contains it, so treating
# it as a signal would match every organisation to every domain.
NOISE_TOKENS = {
    "sarl", "sas", "sasu", "eurl", "sa", "sci", "scop", "scic", "snc", "eirl",
    "association", "asso", "groupe", "group", "cabinet", "centre", "center",
    "institut", "ecole", "academie", "academy", "campus", "conseil",
    "formation", "formations", "consulting", "france", "paris", "de", "du",
    "des", "la", "le", "les", "et", "pour", "the", "and",
}
MIN_TOKEN_LENGTH = 4
# Below this share of the name's significant tokens, the match is a guess.
MIN_COVERAGE = 0.5


def fold(value: str) -> str:
    """Lowercase, strip accents, keep letters and digits."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def significant_tokens(name: str) -> list[str]:
    """The parts of a name that actually identify the organisation."""
    tokens = [t for t in fold(name).split()
              if len(t) >= MIN_TOKEN_LENGTH and t not in NOISE_TOKENS]
    # A name made only of filler ("CENTRE DE FORMATION") identifies nothing;
    # returning [] makes every candidate fail, which is the right answer.
    return tokens


def domain_haystack(domain: str) -> str:
    """The domain without dots, dashes or TLD, for substring matching."""
    host = domain.lower()
    host = re.sub(r"^www\.", "", host)
    host = host.rsplit(".", 1)[0] if "." in host else host
    return re.sub(r"[^a-z0-9]+", "", host)


def coverage(name: str, domain: str) -> float:
    """Share of the name's identifying tokens present in the domain."""
    tokens = significant_tokens(name)
    if not tokens:
        return 0.0
    haystack = domain_haystack(domain)
    return sum(1 for t in tokens if t in haystack) / len(tokens)


def best_domain(name: str, candidates: list[str], *,
                refuse: callable | None = None) -> tuple[str, float] | None:
    """The candidate whose domain carries the organisation's name, or None.

    Ties and weak matches return None on purpose. Leaving a company without a
    domain costs one prospect; attaching the wrong one corrupts the record and
    eventually sends mail to the wrong company.
    """
    scored: list[tuple[float, str]] = []
    for domain in candidates:
        domain = (domain or "").strip().lower()
        if not domain or "." not in domain:
            continue
        if refuse is not None and refuse(domain):
            continue
        score = coverage(name, domain)
        if score >= MIN_COVERAGE:
            scored.append((score, domain))
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], len(pair[1])))
    best, runner_up = scored[0], scored[1] if len(scored) > 1 else None
    # Two candidates matching the name equally well is an ambiguity, not a
    # result — a franchise and its head office, or two unrelated namesakes.
    if runner_up is not None and abs(runner_up[0] - best[0]) < 1e-9:
        return None
    return best[1], best[0]


async def pending(state: StateManager, limit: int) -> list[dict]:
    """Companies with no domain that are not excluded from prospecting.

    The exclusion belongs in the query, not after the LIMIT. Fetching a window
    and filtering it afterwards let the excluded partners — imported first, so
    oldest — fill the window and crowd out every real candidate, and the tool
    reported "nothing to resolve" with 38 waiting.
    """
    async with state._connect() as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            """SELECT id, name, location, external_id FROM companies
               WHERE domain = '' AND name != ''
                 AND NOT EXISTS (
                       SELECT 1 FROM observations o
                       WHERE o.company_id = companies.id
                         AND o.signal_code = ?)
               ORDER BY created_at LIMIT ?""", (DO_NOT_PROSPECT, limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def set_domain(state: StateManager, company_id: str, domain: str) -> str:
    """Attach the domain, unless another company already holds it."""
    async with state._connect() as db:
        async with db.execute(
            "SELECT id FROM companies WHERE domain = ? AND id != ?", (domain, company_id)
        ) as cursor:
            clash = await cursor.fetchone()
        if clash:
            return "already held by another company"
        await db.execute("UPDATE companies SET domain = ? WHERE id = ?",
                         (domain, company_id))
        await db.commit()
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=20,
                        help="organisations to attempt (default 20)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be attached, change nothing")
    args = parser.parse_args()

    from harvey.agents.scout import Scout
    from harvey.brain import Brain
    from harvey.config import load_config, load_env

    state = StateManager()
    await state.init_db()
    config = load_config()
    scout = Scout(Brain(state, config.brain), state, config, load_env())

    todo = await pending(state, args.limit)
    if not todo:
        print("\n  Nothing to resolve: every company already has a domain.")
        return 0

    resolved = ambiguous = failed = 0
    for row in todo:
        name = row["name"]
        town = (row["location"] or "").split()[-1] if row["location"] else ""
        # The register gives a town; it is what separates two namesakes.
        results = await scout._web_search(f"{name} {town} site officiel".strip())
        candidates = [scout._extract_domain(url) for url, _ in results]
        pick = best_domain(name, candidates, refuse=scout._is_noise_domain)

        if pick is None:
            if candidates:
                ambiguous += 1
                print(f"  ?  {name[:38]:38} aucun domaine assez sûr "
                      f"({len(candidates)} candidats)")
            else:
                failed += 1
                print(f"  ×  {name[:38]:38} aucun résultat de recherche")
            continue

        domain, score = pick
        if args.dry_run:
            resolved += 1
            print(f"  →  {name[:38]:38} {domain:32} (couverture {score:.0%})")
            continue
        problem = await set_domain(state, row["id"], domain)
        if problem:
            ambiguous += 1
            print(f"  ?  {name[:38]:38} {domain:32} — {problem}")
        else:
            resolved += 1
            print(f"  ✓  {name[:38]:38} {domain:32} (couverture {score:.0%})")

    print(f"\n  résolus     {resolved}")
    print(f"  ambigus     {ambiguous}   (laissés sans domaine, volontairement)")
    print(f"  introuvables {failed}")
    if args.dry_run:
        print("\n  --dry-run : rien n'a été écrit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
