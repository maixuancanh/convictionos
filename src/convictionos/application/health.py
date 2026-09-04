from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class HealthSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "ready", "not_ready"]
    release_sha: str
    checks: dict[str, str] = {}


class HealthService:
    def __init__(self, engine: AsyncEngine, *, release_sha: str) -> None:
        self._engine = engine
        self._release_sha = release_sha

    def live(self) -> HealthSnapshot:
        return HealthSnapshot(status="ok", release_sha=self._release_sha)

    async def ready(self) -> HealthSnapshot:
        checks: dict[str, str] = {}
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                checks["database"] = "ok"
                try:
                    version = await connection.scalar(
                        text(
                            "SELECT version_num FROM alembic_version "
                            "ORDER BY version_num DESC LIMIT 1"
                        )
                    )
                    checks["migration"] = str(version) if version else "unversioned"
                except SQLAlchemyError:
                    checks["migration"] = "unversioned"
        except SQLAlchemyError:
            checks["database"] = "unavailable"
            checks["migration"] = "unavailable"
        status: Literal["ready", "not_ready"] = (
            "ready" if checks.get("database") == "ok" else "not_ready"
        )
        if checks.get("migration") not in {None, "unavailable", "unversioned"}:
            checks["migration"] = "ok"
        return HealthSnapshot(
            status=status,
            release_sha=self._release_sha,
            checks=checks,
        )
