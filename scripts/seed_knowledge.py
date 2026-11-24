"""Load the NUC directory of all approved universities; preserve existing administrator records.

No scores, admission cycles, dates, course requirements or accreditation claims
are fabricated. The application also runs this check at start-up.
"""

import asyncio

from app.db.session import AsyncSessionLocal
from app.services.directory import seed

__all__ = ["seed", "main"]


async def main():
    async with AsyncSessionLocal() as db:
        added = await seed(db)
        await db.commit()
    print(
        f"Added {added} universities; existing records preserved. No admission scores or dates added."
    )


if __name__ == "__main__":
    asyncio.run(main())
