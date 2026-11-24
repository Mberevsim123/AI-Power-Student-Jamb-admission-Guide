"""Promote an existing consenting account; never creates a default password.

Run: python -m scripts.create_admin student@example.com
"""

import argparse
import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal, engine
from app.models.user import User


async def main(email):
    try:
        async with AsyncSessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email))
            if not user:
                raise SystemExit("Register the account through the API first.")
            if not user.data_consent_at:
                raise SystemExit("The account must give data processing consent first.")
            user.role = "admin"
            await db.commit()
            print("Administrator role granted.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    asyncio.run(main(parser.parse_args().email))
