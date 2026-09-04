from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.application.runtime_coordinator import RuntimeCoordinator
from convictionos.domain.runtime import RuntimeControlState
from convictionos.infrastructure.runtime_store import RuntimeStore


def coordinator(engine: AsyncEngine, *, owner: str = "worker-a") -> RuntimeCoordinator:
    store = RuntimeStore(async_sessionmaker(engine, expire_on_commit=False))
    return RuntimeCoordinator(
        store,
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        owner_instance_id=owner,
        release_sha="f" * 40,
    )


@pytest.mark.asyncio
async def test_coordinator_acquires_lease_records_heartbeat_and_starts_cycle(
    engine: AsyncEngine,
) -> None:
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    runtime = coordinator(engine)

    lease = await runtime.acquire(now=now)
    cycle = await runtime.start_cycle(
        now=now + timedelta(seconds=1),
        market_session_state="open",
        next_scheduled_at=now + timedelta(minutes=5),
    )

    assert lease.owner_instance_id == "worker-a"
    assert cycle.fencing_token == lease.fencing_token
    status = await runtime.status()
    assert status.lease_owner_instance_id == "worker-a"
    assert status.last_heartbeat_at == now
    assert status.last_cycle_state == "started"


@pytest.mark.asyncio
async def test_coordinator_refuses_financial_cycle_when_lease_nearly_expired(
    engine: AsyncEngine,
) -> None:
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    runtime = coordinator(engine)
    await runtime.acquire(now=now)

    with pytest.raises(RuntimeError, match="lease is not fresh enough"):
        await runtime.start_cycle(
            now=now + timedelta(seconds=46),
            market_session_state="open",
            next_scheduled_at=None,
        )


@pytest.mark.asyncio
async def test_coordinator_surfaces_persistent_closing_only_control(
    engine: AsyncEngine,
) -> None:
    runtime = coordinator(engine)
    await runtime.set_control(
        state=RuntimeControlState.CLOSING_ONLY,
        reason="risk breaker",
        actor="risk",
        effective_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        recovery_condition="resolve incident",
    )

    decision = await runtime.control_decision(underlying="SPY")

    assert decision.allow_new_entries is False
    assert decision.allow_closing_submissions is True
    assert decision.control_state is RuntimeControlState.CLOSING_ONLY
