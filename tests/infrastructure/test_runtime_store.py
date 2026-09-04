from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.domain.runtime import (
    AgentCycleState,
    RuntimeControl,
    RuntimeControlState,
    RuntimeScopeType,
)
from convictionos.infrastructure.runtime_store import RuntimeStore, StaleSchedulerLease


def runtime_store(engine: AsyncEngine) -> RuntimeStore:
    return RuntimeStore(async_sessionmaker(engine, expire_on_commit=False))


@pytest.mark.asyncio
async def test_scheduler_lease_is_exclusive_and_takeover_uses_new_fencing_token(
    engine: AsyncEngine,
) -> None:
    store = runtime_store(engine)
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)

    first = await store.acquire_lease(
        lease_name="agent:workspace-001:paper-account",
        owner_instance_id="worker-a",
        release_sha="f" * 40,
        now=now,
    )
    blocked = await store.acquire_lease(
        lease_name="agent:workspace-001:paper-account",
        owner_instance_id="worker-b",
        release_sha="f" * 40,
        now=now + timedelta(seconds=30),
    )
    takeover = await store.acquire_lease(
        lease_name="agent:workspace-001:paper-account",
        owner_instance_id="worker-b",
        release_sha="f" * 40,
        now=now + timedelta(seconds=91),
    )

    assert first is not None
    assert blocked is None
    assert takeover is not None
    assert takeover.owner_instance_id == "worker-b"
    assert takeover.fencing_token == first.fencing_token + 1


@pytest.mark.asyncio
async def test_stale_fencing_token_cannot_record_financial_cycle(
    engine: AsyncEngine,
) -> None:
    store = runtime_store(engine)
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    lease = await store.acquire_lease(
        lease_name="agent:workspace-001:paper-account",
        owner_instance_id="worker-a",
        release_sha="f" * 40,
        now=now,
    )
    assert lease is not None
    takeover = await store.acquire_lease(
        lease_name=lease.lease_name,
        owner_instance_id="worker-b",
        release_sha="f" * 40,
        now=now + timedelta(seconds=91),
    )
    assert takeover is not None

    with pytest.raises(StaleSchedulerLease):
        await store.record_cycle_started(
            lease_name=lease.lease_name,
            owner_instance_id="worker-a",
            fencing_token=lease.fencing_token,
            cycle_id="cycle-stale",
            workspace_id="workspace-001",
            account_id="paper-account",
            agent_id="convictionos",
            release_sha="f" * 40,
            started_at=now + timedelta(seconds=92),
            market_session_state="open",
            next_scheduled_at=None,
        )


@pytest.mark.asyncio
async def test_cycle_history_is_persisted_with_safe_error_type(engine: AsyncEngine) -> None:
    store = runtime_store(engine)
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    lease = await store.acquire_lease(
        lease_name="agent:workspace-001:paper-account",
        owner_instance_id="worker-a",
        release_sha="f" * 40,
        now=now,
    )
    assert lease is not None

    cycle = await store.record_cycle_started(
        lease_name=lease.lease_name,
        owner_instance_id="worker-a",
        fencing_token=lease.fencing_token,
        cycle_id="cycle-001",
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        release_sha="f" * 40,
        started_at=now,
        market_session_state="open",
        next_scheduled_at=now + timedelta(minutes=5),
    )
    await store.record_cycle_finished(
        cycle_id=cycle.cycle_id,
        owner_instance_id="worker-a",
        fencing_token=lease.fencing_token,
        state=AgentCycleState.ERROR,
        finished_at=now + timedelta(seconds=3),
        safe_error_type="ValueError",
        lifecycle_state="review",
        entry_state=None,
    )

    latest = await store.latest_cycles("workspace-001", "paper-account", limit=1)

    assert len(latest) == 1
    assert latest[0].cycle_id == "cycle-001"
    assert latest[0].state is AgentCycleState.ERROR
    assert latest[0].safe_error_type == "ValueError"
    assert "secret" not in latest[0].model_dump_json()


@pytest.mark.asyncio
async def test_effective_runtime_control_uses_most_restrictive_scope(
    engine: AsyncEngine,
) -> None:
    store = runtime_store(engine)
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    await store.put_control(
        RuntimeControl(
            control_id="global-running",
            scope_type=RuntimeScopeType.GLOBAL,
            scope_id="*",
            state=RuntimeControlState.RUNNING,
            reason="default",
            actor="system",
            policy_version="runtime-policy-v1",
            effective_at=now,
            recovery_condition=None,
            version=1,
        )
    )
    await store.put_control(
        RuntimeControl(
            control_id="account-closing",
            scope_type=RuntimeScopeType.ACCOUNT,
            scope_id="paper-account",
            state=RuntimeControlState.CLOSING_ONLY,
            reason="risk review",
            actor="risk",
            policy_version="runtime-policy-v1",
            effective_at=now + timedelta(seconds=1),
            recovery_condition="incident resolved",
            version=1,
        )
    )

    control = await store.effective_control(
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        underlying="SPY",
    )

    assert control is not None
    assert control.state is RuntimeControlState.CLOSING_ONLY
    assert control.allows_new_entries is False
    assert control.allows_closing_submissions is True
