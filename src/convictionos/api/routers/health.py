from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from convictionos.application.health import HealthService
from convictionos.observability import metric_line


def create_health_router(
    health: HealthService,
    *,
    release_sha: str,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live")
    async def live() -> dict[str, object]:
        return health.live().model_dump(mode="json", exclude={"checks"})

    @router.get("/health/ready")
    async def ready() -> dict[str, object]:
        return (await health.ready()).model_dump(mode="json")

    @router.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return "\n".join(
            [
                "# HELP convictionos_release_info Release identity.",
                "# TYPE convictionos_release_info gauge",
                metric_line(
                    "convictionos_release_info",
                    1,
                    {"release_sha": release_sha},
                ),
                "",
            ]
        )

    return router
