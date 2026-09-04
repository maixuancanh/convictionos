from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def verify(database_url: str, *, mutation_disabled: bool) -> dict[str, object]:
    if not mutation_disabled:
        raise ValueError("restore verification must run with --mutation-disabled")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            migration = await connection.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            runtime_controls = await connection.scalar(
                text("SELECT COUNT(*) FROM runtime_controls")
            )
            incidents = await connection.scalar(
                text("SELECT COUNT(*) FROM runtime_incidents")
            )
    finally:
        await engine.dispose()
    return {
        "migration": migration,
        "runtime_controls": runtime_controls,
        "runtime_incidents": incidents,
        "mutation_disabled": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--database", help="Deprecated; use --database-url")
    parser.add_argument("--mutation-disabled", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        verify(args.database_url, mutation_disabled=args.mutation_disabled)
    )
    print(result)


if __name__ == "__main__":
    main()
