"""Load the NUC directory of all approved universities; preserve existing administrator records.

No scores, admission cycles, dates, course requirements or accreditation claims
are fabricated. Baseline JAMB process guides are bundled with the application.
"""

import asyncio

from sqlalchemy import or_, select

from app.db.session import AsyncSessionLocal
from app.models.admission import University
from app.services.local_knowledge import DIRECTORY_REVIEWED_ON, NUC_SOURCES, UNIVERSITIES


async def seed(db):
    added = 0
    for name, abbr, state, kind, website, aliases, established, nuc_name in UNIVERSITIES:
        # Compare abbreviations only when present: "abbreviation = NULL" must not match.
        match = University.name == name
        if abbr:
            match = or_(match, University.abbreviation == abbr)
        if await db.scalar(select(University.id).where(match)):
            continue
        db.add(
            University(
                name=name,
                abbreviation=abbr,
                state=state,
                university_type=kind,
                website_url=website,
                admissions_meta={
                    "source_url": NUC_SOURCES[kind],
                    "reviewed_on": DIRECTORY_REVIEWED_ON,
                    "aliases": aliases,
                    "established": established,
                    "nuc_name": nuc_name,
                    "state_basis": "institution location (the NUC table has no state column)",
                    "scope": "institution identity only; admission requirements are not supplied",
                },
            )
        )
        await db.flush()
        added += 1
    return added


async def main():
    async with AsyncSessionLocal() as db:
        added = await seed(db)
        await db.commit()
    print(
        f"Added {added} universities; existing records preserved. No admission scores or dates added."
    )


if __name__ == "__main__":
    asyncio.run(main())
