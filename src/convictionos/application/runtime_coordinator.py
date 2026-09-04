from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from convictionos.domain.runtime import (
    AgentCycle,
    AgentCycleState,
    RuntimeControl,
    RuntimeControlDecision,
    RuntimeControlState,
    RuntimeScopeType,
    RuntimeStatus,
    SchedulerLease,
)
from convictionos.infrastructure.runtime_store import RuntimeStore, StaleSchedulerLease


class RuntimeCoordinatorDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allow_new_entries: bool
    allow_closing_submissions: bool
    control_state: RuntimeControlState
    public_reasons: tuple[str, ...]


class RuntimeCoordinator:
    def __init__(
        self,
        store: RuntimeStore,
        *,
        workspace_id: str,
        account_id: str,
        agent_id: str,
        owner_instance_id: str,
        release_sha: str,
    ) -> None:
        self._store = store
        self._workspace_id = workspace_id
        self._account_id = account_id
        self._agent_id = agent_id
        self._owner_instance_id = owner_instance_id
        self._release_sha = release_sha
        self._lease_name = f"agent:{workspace_id}:{account_id}:{agent_id}"
        self._lease: SchedulerLease | None = None

    @property
    def lease_name(self) -> str:
        return self._lease_name

    async def acquire(self, *, now: datetime) -> SchedulerLease:
        lease = await self._store.acquire_lease(
            lease_name=self._lease_name,
            owner_instance_id=self._owner_instance_id,
            release_sha=self._release_sha,
            now=now,
        )
        if lease is None:
            raise RuntimeError("scheduler lease is held by another worker")
        self._lease = lease
        return lease

    async def renew(self, *, now: datetime) -> SchedulerLease:
        lease = self._require_lease()
        self._lease = await self._store.renew_lease(
            lease_name=lease.lease_name,
            owner_instance_id=self._owner_instance_id,
            fencing_token=lease.fencing_token,
            release_sha=self._release_sha,
            now=now,
        )
        return self._lease

    async def start_cycle(
        self,
        *,
        now: datetime,
        market_session_state: str,
        next_scheduled_at: datetime | None,
    ) -> AgentCycle:
        lease = self._require_lease()
        if not lease.can_start_financial_cycle(now=now):
            raise RuntimeError("lease is not fresh enough to start financial cycle")
        try:
            return await self._store.record_cycle_started(
                lease_name=lease.lease_name,
                owner_instance_id=self._owner_instance_id,
                fencing_token=lease.fencing_token,
                cycle_id=f"cycle-{uuid4().hex}",
                workspace_id=self._workspace_id,
                account_id=self._account_id,
                agent_id=self._agent_id,
                release_sha=self._release_sha,
                started_at=now,
                market_session_state=market_session_state,
                next_scheduled_at=next_scheduled_at,
            )
        except StaleSchedulerLease as error:
            raise RuntimeError("scheduler lease is stale") from error

    async def finish_cycle(
        self,
        cycle: AgentCycle,
        *,
        state: AgentCycleState,
        finished_at: datetime,
        safe_error_type: str | None = None,
        lifecycle_state: str | None = None,
        entry_state: str | None = None,
    ) -> AgentCycle:
        lease = self._require_lease()
        return await self._store.record_cycle_finished(
            cycle_id=cycle.cycle_id,
            owner_instance_id=self._owner_instance_id,
            fencing_token=lease.fencing_token,
            state=state,
            finished_at=finished_at,
            safe_error_type=safe_error_type,
            lifecycle_state=lifecycle_state,
            entry_state=entry_state,
        )

    async def set_control(
        self,
        *,
        state: RuntimeControlState,
        reason: str,
        actor: str,
        effective_at: datetime,
        recovery_condition: str | None,
    ) -> RuntimeControl:
        return await self._store.put_control(
            RuntimeControl(
                control_id=f"control-{uuid4().hex}",
                scope_type=RuntimeScopeType.ACCOUNT,
                scope_id=self._account_id,
                state=state,
                reason=reason,
                actor=actor,
                policy_version="runtime-policy-v1",
                effective_at=effective_at,
                recovery_condition=recovery_condition,
                version=1,
            )
        )

    async def control_decision(self, *, underlying: str | None = None) -> RuntimeControlDecision:
        control = await self._store.effective_control(
            workspace_id=self._workspace_id,
            account_id=self._account_id,
            agent_id=self._agent_id,
            underlying=underlying,
        )
        if control is None:
            return RuntimeControlDecision(
                control_state=RuntimeControlState.RUNNING,
                public_reasons=(),
                allow_new_entries=True,
                allow_closing_submissions=True,
                control_id=None,
            )
        return RuntimeControlDecision(
            control_state=control.state,
            public_reasons=(control.reason,),
            allow_new_entries=control.allows_new_entries,
            allow_closing_submissions=control.allows_closing_submissions,
            control_id=control.control_id,
        )

    async def status(self) -> RuntimeStatus:
        return await self._store.status(
            lease_name=self._lease_name,
            workspace_id=self._workspace_id,
            account_id=self._account_id,
            agent_id=self._agent_id,
        )

    def _require_lease(self) -> SchedulerLease:
        if self._lease is None:
            raise RuntimeError("scheduler lease has not been acquired")
        return self._lease
