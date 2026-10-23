"""Import a list of clients or current partners as a never-prospect list.

Not a file kept beside Harvey: each row becomes a company in Harvey's own
database carrying the reserved DO_NOT_PROSPECT signal, so every path honours
it — the cohort builder and, just as importantly, the contacts backfill, which
does not go through cohort() and would otherwise go looking for the
decision-maker at an organisation that has already signed.

Idempotent and non-destructive. Re-running adds nothing and deletes nothing,
so it is safe to point at a database an earlier run already touched.

Domains are reduced to a bare host. Harvey's own normalisation only strips and
lowercases, so "https://www.x.fr/" and "x.fr" are two different companies and
an exclusion marked on one would miss the other. Rows already stored as a URL
by an earlier import are repaired in place.

The file is delimiter-separated with a header. Recognised columns, all
optional except a name:

    name | domain | external_id | note

Common aliases are accepted (organisme, raison_sociale, site, siret, nda,
statut), so an export from a CRM or a Notion view usually needs no editing.

    HARVEY_HOME=~/.harvey-acme python tools/import_exclusions.py clients.csv
    ... --delimiter , --label "Existing client" --source crm:export
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sqlite3

from harvey.models.company import Company
from harvey.state import DO_NOT_PROSPECT, StateManager

# First match wins, so put the most specific alias first.
NAME_KEYS = ("name", "organisme", "raison_sociale", "raison sociale", "company")
DOMAIN_KEYS = ("domain", "site", "site_internet", "site internet", "website", "url")
ID_KEYS = ("external_id", "nda", "siret", "siren", "id")
NOTE_KEYS = ("note", "statut", "status", "commentaire")


def pick(row: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        for actual, value in row.items():
            if actual and actual.strip().lower() == key and (value or "").strip():
                return value.strip()
    return ""


def bare_host(value: str) -> str:
    """Reduce a URL to its host: no scheme, no www., no path."""
    text = (value or "").strip().lower()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
    text = text.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    return text[4:] if text.startswith("www.") else text


async def already_marked(state: StateManager, company_id: str, code: str) -> bool:
    return bool(await state.get_observations(
        company_id=company_id, signal_code=code, limit=1))


async def repair_domains(state: StateManager, source: str) -> int:
    """Normalise domains this import owns, leaving discovered companies alone."""
    repaired = 0
    async with state._connect() as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT id, domain FROM companies WHERE source = ? AND domain != ''",
            (source,),
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
        for row in rows:
            clean = bare_host(row["domain"])
            if clean == row["domain"]:
                continue
            try:
                await db.execute(
                    "UPDATE companies SET domain = ? WHERE id = ?", (clean, row["id"]))
                repaired += 1
            except sqlite3.IntegrityError:
                # Another row already holds the bare host. Both get marked;
                # nothing is deleted.
                pass
        await db.commit()
    return repaired


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="delimiter-separated file with a header row")
    parser.add_argument("--delimiter", default="|", help="column separator (default |)")
    parser.add_argument("--label", default="Existing client or partner",
                        help="label for the business-fact signal")
    parser.add_argument("--code", default="EXISTING_RELATIONSHIP",
                        help="code for the business-fact signal")
    parser.add_argument("--source", default="import:exclusions",
                        help="recorded as the companies' source")
    parser.add_argument("--evidence", default="", help="URL of the list of record")
    args = parser.parse_args()

    state = StateManager()
    await state.init_db()

    for code, label, description in (
        (args.code, args.label, "Imported from a list of existing relationships."),
        (DO_NOT_PROSPECT, "Do not prospect",
         "Excluded from all cold prospecting, the contacts backfill included."),
    ):
        await state.upsert_signal_code(
            code, label=label, description=description, category="relation",
            value_type="text", collector="import", cost_note="free",
            status="confirmed",
        )

    repaired = await repair_domains(state, args.source)

    seen = marked = skipped = 0
    with open(args.path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter=args.delimiter):
            name = pick(row, NAME_KEYS)
            if not name:
                skipped += 1
                continue
            seen += 1
            identifier = pick(row, ID_KEYS)
            note = pick(row, NOTE_KEYS) or "no status"
            company_id = await state.add_company(Company(
                name=name,
                domain=bare_host(pick(row, DOMAIN_KEYS)),
                # A business with no website still has to dedup across re-runs.
                external_id=f"excl:{identifier}" if identifier else f"excl:{name}",
                source=args.source,
            ))
            for code in (args.code, DO_NOT_PROSPECT):
                if await already_marked(state, company_id, code):
                    continue
                await state.add_observation(
                    code, company_id=company_id, collector="import",
                    value_text=note, evidence_url=args.evidence)
                marked += 1

    excluded = await state.cohort(require=[DO_NOT_PROSPECT], limit=5000)
    remaining = await state.companies_needing_prospects(limit=5000)
    leaking = [c for c in remaining if c["id"] in set(excluded)]

    print(f"\n  rows read                      {seen}")
    print(f"  rows without a name (skipped)  {skipped}")
    print(f"  domains repaired (URL -> host) {repaired}")
    print(f"  observations added             {marked}  (0 on a second run)")
    print(f"  companies marked do-not-prospect {len(excluded)}")
    print(f"  still prospectable             {len(remaining)}")
    print(f"  marked but still prospectable  {len(leaking)}  (must be 0)")
    return 0 if not leaking else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
