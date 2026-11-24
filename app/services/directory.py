"""Keep the NUC university directory present in the database.

Runs in the release step and again at application start, so a fresh database or a
skipped release always ends up with every approved university. It only inserts
institutions that are missing (matched by name or abbreviation) and never edits or
removes administrator records. No scores, dates or requirements are created.
"""

import logging

from sqlalchemy import func, select, text

from app.models.admission import University
from app.services.local_knowledge import DIRECTORY_REVIEWED_ON, NUC_SOURCES, UNIVERSITIES

logger = logging.getLogger(__name__)
# Serialises concurrent loaders (several web workers starting at once).
DIRECTORY_LOCK_ID = 4_207_202_611


async def seed(db):
    """Insert missing directory universities; return how many were added."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DIRECTORY_LOCK_ID})
    rows = (await db.execute(select(University.name, University.abbreviation))).all()
    names = {name.casefold() for name, _ in rows}
    abbreviations = {abbr.casefold() for _, abbr in rows if abbr}
    added = 0
    for name, abbr, state, kind, website, aliases, established, nuc_name in UNIVERSITIES:
        if name.casefold() in names or (abbr and abbr.casefold() in abbreviations):
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
        names.add(name.casefold())
        if abbr:
            abbreviations.add(abbr.casefold())
        added += 1
    await db.flush()
    return added


async def ensure_directory(session_factory):
    """Startup self-heal: load any missing universities; never block the app on failure."""
    try:
        async with session_factory() as db:
            present = await db.scalar(select(func.count()).select_from(University))
            if present >= len(UNIVERSITIES):
                return 0
            added = await seed(db)
            await db.commit()
            if added:
                logger.info("University directory: added %d missing institution(s)", added)
            return added
    except Exception:
        logger.exception("University directory check failed; the app will continue")
        return 0
