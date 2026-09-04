from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from convictionos.api.main import ApplicationContainer, build_container
from convictionos.application.agent_scheduler import AgentScheduler
from convictionos.application.runtime_coordinator import RuntimeCoordinator
from convictionos.settings import Settings


@dataclass(frozen=True)
class WorkerRuntime:
    scheduler: AgentScheduler
    runtime_coordinator: RuntimeCoordinator
    release_sha: str


def build_worker_runtime(container: ApplicationContainer) -> WorkerRuntime:
    if container.scheduler is None or container.runtime_coordinator is None:
        raise ValueError("worker role requires an enabled paper agent")
    return WorkerRuntime(
        scheduler=container.scheduler,
        runtime_coordinator=container.runtime_coordinator,
        release_sha=container.settings.runtime_release_sha,
    )


async def run_worker(settings: Settings | None = None) -> None:
    container = build_container(settings or Settings())
    runtime = build_worker_runtime(container)
    await runtime.runtime_coordinator.acquire(now=datetime.now(UTC))
    renewer = start_runtime_lease_renewer(runtime.runtime_coordinator)
    runtime.scheduler.start(interval_seconds=container.settings.agent_interval_seconds)
    print(
        "convictionos_worker_started "
        f"release_sha={runtime.release_sha} "
        f"instance_id={container.settings.runtime_instance_id} "
        f"agent_interval_seconds={container.settings.agent_interval_seconds}"
    )
    try:
        await asyncio.Event().wait()
    finally:
        renewer.cancel()
        try:
            await renewer
        except asyncio.CancelledError:
            pass
        await runtime.scheduler.stop()


def start_runtime_lease_renewer(
    runtime_coordinator: RuntimeCoordinator,
    *,
    interval_seconds: float = 30.0,
    now_factory: Callable[[], datetime] | None = None,
) -> asyncio.Task[None]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    active_now_factory = now_factory or (lambda: datetime.now(UTC))
    return asyncio.create_task(
        renew_runtime_lease_forever(
            runtime_coordinator,
            interval_seconds=interval_seconds,
            now_factory=active_now_factory,
        )
    )


async def renew_runtime_lease_forever(
    runtime_coordinator: RuntimeCoordinator,
    *,
    interval_seconds: float,
    now_factory: Callable[[], datetime],
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await runtime_coordinator.renew(now=now_factory())
