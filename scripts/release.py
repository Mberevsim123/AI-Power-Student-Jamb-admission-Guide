"""Release step: apply database migrations, then load the university directory.

Run once per deployment, before traffic moves to the new release:

    python -m scripts.release

A single command (not a shell chain such as ``a && b``) so it behaves the same on
every platform, including pre-deploy runners that do not invoke a shell. Both
phases are idempotent; any failure exits non-zero so the deployment is stopped
and the previous release keeps serving.
"""

import asyncio
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    print("release: applying database migrations", flush=True)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    print("release: loading the NUC university directory", flush=True)
    from scripts.seed_knowledge import main as seed_directory

    asyncio.run(seed_directory())
    print("release: complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
