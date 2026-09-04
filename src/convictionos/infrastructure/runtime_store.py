from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from convictionos.domain.runtime import (
    AgentCycle,
    AgentCycleState,
    RuntimeControl,
    RuntimeControlState,
    RuntimeScopeType,
    RuntimeStatus,
    SchedulerLease,
)
from convictionos.infrastructure.models import (
    AgentCycleRow,
    RuntimeControlRow,
    SchedulerHeartbeatRow,
    SchedulerLeaseRow,
)


class StaleSchedulerLease(RuntimeError):
    pass


class RuntimeStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = 90,
    ) -> None:
        self._sessions = sessions
        self._lease_seconds = lease_seconds

    async def acquire_lease(
        self,
        *,
        lease_name: str,
        owner_instance_id: str,
        release_sha: str,
        now: datetime,
    ) -> SchedulerLease | None:
        expires_at = now + timedelta(seconds=self._lease_seconds)
        async with self._sessions() as session:
            row = await session.get(
                SchedulerLeaseRow, lease_name, with_for_update=True
            )
            if row is None:
                row = SchedulerLeaseRow(
                    lease_name=lease_name,
                    owner_instance_id=owner_instance_id,
                    fencing_token=1,
                    acquired_at=now,
                    renewed_at=now,
                    expires_at=expires_at,
                    release_sha=release_sha,
                    version=1,
                )
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return None
                await self._write_heartbeat(session, row, now=now)
                await session.commit()
                return _lease(row)

            previous_owner = row.owner_instance_id
            if previous_owner != owner_instance_id and row.expires_at > now:
                await session.rollback()
                return None

            row.owner_instance_id = owner_instance_id
            if previous_owner != owner_instance_id:
                row.fencing_token += 1
            row.renewed_at = now
            row.expires_at = expires_at
            row.release_sha = release_sha
            row.version += 1
            await self._write_heartbeat(session, row, now=now)
            await session.commit()
            return _lease(row)

    async def renew_lease(
        self,
        *,
        lease_name: str,
        owner_instance_id: str,
        fencing_token: int,
        release_sha: str,
        now: datetime,
    ) -> SchedulerLease:
        async with self._sessions() as session:
            row = await self._locked_live_lease(
                session,
                lease_name=lease_name,
                owner_instance_id=owner_instance_id,
                fencing_token=fencing_token,
                now=now,
            )
            row.renewed_at = now
            row.expires_at = now + timedelta(seconds=self._lease_seconds)
            row.release_sha = release_sha
            row.version += 1
            await self._write_heartbeat(session, row, now=now)
            await session.commit()
            return _lease(row)

    async def current_lease(self, lease_name: str) -> SchedulerLease | None:
        async with self._sessions() as session:
            row = await session.get(SchedulerLeaseRow, lease_name)
            return _lease(row) if row is not None else None

    async def put_control(self, control: RuntimeControl) -> RuntimeControl:
        async with self._sessions() as session:
            row = await session.get(RuntimeControlRow, control.control_id)
            if row is None:
                session.add(_control_row(control))
            else:
                existing = _control(row)
                if existing != control:
                    raise ValueError("runtime control id already exists with different payload")
            await session.commit()
            return control

    async def effective_control(
        self,
        *,
        workspace_id: str,
        account_id: str,
        agent_id: str,
        underlying: str | None,
    ) -> RuntimeControl | None:
        scope_pairs = [
            (RuntimeScopeType.GLOBAL.value, "*"),
            (RuntimeScopeType.WORKSPACE.value, workspace_id),
            (RuntimeScopeType.ACCOUNT.value, account_id),
            (RuntimeScopeType.AGENT.value, agent_id),
        ]
        if underlying is not None:
            scope_pairs.append((RuntimeScopeType.UNDERLYING.value, underlying))
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(RuntimeControlRow)
                    .where(
                        RuntimeControlRow.scope_type.in_([item[0] for item in scope_pairs]),
                        RuntimeControlRow.scope_id.in_([item[1] for item in scope_pairs]),
                    )
                    .order_by(RuntimeControlRow.effective_at.desc())
                )
            ).all()
        controls = [
            _control(row)
            for row in rows
            if (row.scope_type, row.scope_id) in set(scope_pairs)
        ]
        if not controls:
            return None
        return min(controls, key=lambda item: _CONTROL_RANK[item.state])

    async def record_cycle_started(
        self,
        *,
        lease_name: str,
        owner_instance_id: str,
        fencing_token: int,
        cycle_id: str,
        workspace_id: str,
        account_id: str,
        agent_id: str,
        release_sha: str,
        started_at: datetime,
        market_session_state: str,
        next_scheduled_at: datetime | None,
    ) -> AgentCycle:
        async with self._sessions() as session:
            await self._locked_live_lease(
                session,
                lease_name=lease_name,
                owner_instance_id=owner_instance_id,
                fencing_token=fencing_token,
                now=started_at,
            )
            row = AgentCycleRow(
                cycle_id=cycle_id,
                lease_name=lease_name,
                workspace_id=workspace_id,
                account_id=account_id,
                agent_id=agent_id,
                owner_instance_id=owner_instance_id,
                fencing_token=fencing_token,
                release_sha=release_sha,
                started_at=started_at,
                finished_at=None,
                state=AgentCycleState.STARTED.value,
                market_session_state=market_session_state,
                lifecycle_state=None,
                entry_state=None,
                safe_error_type=None,
                next_scheduled_at=next_scheduled_at,
            )
            session.add(row)
            await session.commit()
            return _cycle(row)

    async def record_cycle_finished(
        self,
        *,
        cycle_id: str,
        owner_instance_id: str,
        fencing_token: int,
        state: AgentCycleState,
        finished_at: datetime,
        safe_error_type: str | None,
        lifecycle_state: str | None,
        entry_state: str | None,
    ) -> AgentCycle:
        async with self._sessions() as session:
            row = await session.get(AgentCycleRow, cycle_id, with_for_update=True)
            if row is None:
                raise KeyError(cycle_id)
            if (
                row.owner_instance_id != owner_instance_id
                or row.fencing_token != fencing_token
            ):
                raise StaleSchedulerLease("cycle fencing token is no longer current")
            row.state = state.value
            row.finished_at = finished_at
            row.safe_error_type = safe_error_type
            row.lifecycle_state = lifecycle_state
            row.entry_state = entry_state
            await session.commit()
            return _cycle(row)

    async def latest_cycles(
        self, workspace_id: str, account_id: str, *, limit: int = 20
    ) -> tuple[AgentCycle, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentCycleRow)
                    .where(
                        AgentCycleRow.workspace_id == workspace_id,
                        AgentCycleRow.account_id == account_id,
                    )
                    .order_by(AgentCycleRow.started_at.desc())
                    .limit(limit)
                )
            ).all()
            return tuple(_cycle(row) for row in rows)

    async def status(
        self,
        *,
        lease_name: str,
        workspace_id: str,
        account_id: str,
        agent_id: str,
    ) -> RuntimeStatus:
        async with self._sessions() as session:
            lease_row = await session.get(SchedulerLeaseRow, lease_name)
            heartbeat_row = await session.scalar(
                select(SchedulerHeartbeatRow)
                .where(SchedulerHeartbeatRow.lease_name == lease_name)
                .order_by(SchedulerHeartbeatRow.observed_at.desc())
                .limit(1)
            )
            cycle_row = await session.scalar(
                select(AgentCycleRow)
                .where(
                    AgentCycleRow.workspace_id == workspace_id,
                    AgentCycleRow.account_id == account_id,
                    AgentCycleRow.agent_id == agent_id,
                )
                .order_by(AgentCycleRow.started_at.desc())
                .limit(1)
            )
        return RuntimeStatus(
            lease_name=lease_name,
            lease_owner_instance_id=lease_row.owner_instance_id if lease_row else None,
            fencing_token=lease_row.fencing_token if lease_row else None,
            lease_expires_at=lease_row.expires_at if lease_row else None,
            last_heartbeat_at=heartbeat_row.observed_at if heartbeat_row else None,
            last_cycle_state=cycle_row.state if cycle_row else None,
            effective_control_state=RuntimeControlState.RUNNING,
        )

    async def _locked_live_lease(
        self,
        session: AsyncSession,
        *,
        lease_name: str,
        owner_instance_id: str,
        fencing_token: int,
        now: datetime,
    ) -> SchedulerLeaseRow:
        row = await session.get(SchedulerLeaseRow, lease_name, with_for_update=True)
        if (
            row is None
            or row.owner_instance_id != owner_instance_id
            or row.fencing_token != fencing_token
            or row.expires_at <= now
        ):
            raise StaleSchedulerLease("scheduler lease is stale or not owned")
        return row

    async def _write_heartbeat(
        self, session: AsyncSession, row: SchedulerLeaseRow, *, now: datetime
    ) -> None:
        heartbeat = await session.get(
            SchedulerHeartbeatRow, (row.lease_name, row.owner_instance_id)
        )
        payload = {
            "lease_name": row.lease_name,
            "release_sha": row.release_sha,
            "fencing_token": row.fencing_token,
        }
        if heartbeat is None:
            session.add(
                SchedulerHeartbeatRow(
                    lease_name=row.lease_name,
                    owner_instance_id=row.owner_instance_id,
                    fencing_token=row.fencing_token,
                    release_sha=row.release_sha,
                    observed_at=now,
                    payload=payload,
                )
            )
        else:
            heartbeat.fencing_token = row.fencing_token
            heartbeat.release_sha = row.release_sha
            heartbeat.observed_at = now
            heartbeat.payload = payload


_CONTROL_RANK = {
    RuntimeControlState.FROZEN_REVIEW: 0,
    RuntimeControlState.PAUSED: 1,
    RuntimeControlState.CLOSING_ONLY: 2,
    RuntimeControlState.RUNNING: 3,
}


def _lease(row: SchedulerLeaseRow) -> SchedulerLease:
    return SchedulerLease(
        lease_name=row.lease_name,
        owner_instance_id=row.owner_instance_id,
        fencing_token=row.fencing_token,
        acquired_at=row.acquired_at,
        renewed_at=row.renewed_at,
        expires_at=row.expires_at,
        release_sha=row.release_sha,
        version=row.version,
    )


def _cycle(row: AgentCycleRow) -> AgentCycle:
    return AgentCycle(
        cycle_id=row.cycle_id,
        lease_name=row.lease_name,
        workspace_id=row.workspace_id,
        account_id=row.account_id,
        agent_id=row.agent_id,
        owner_instance_id=row.owner_instance_id,
        fencing_token=row.fencing_token,
        release_sha=row.release_sha,
        started_at=row.started_at,
        finished_at=row.finished_at,
        state=AgentCycleState(row.state),
        market_session_state=row.market_session_state,
        lifecycle_state=row.lifecycle_state,
        entry_state=row.entry_state,
        safe_error_type=row.safe_error_type,
        next_scheduled_at=row.next_scheduled_at,
    )


def _control(row: RuntimeControlRow) -> RuntimeControl:
    return RuntimeControl(
        control_id=row.control_id,
        scope_type=RuntimeScopeType(row.scope_type),
        scope_id=row.scope_id,
        state=RuntimeControlState(row.state),
        reason=row.reason,
        actor=row.actor,
        policy_version=row.policy_version,
        effective_at=row.effective_at,
        recovery_condition=row.recovery_condition,
        version=row.version,
    )


def _control_row(control: RuntimeControl) -> RuntimeControlRow:
    return RuntimeControlRow(
        control_id=control.control_id,
        scope_type=control.scope_type.value,
        scope_id=control.scope_id,
        state=control.state.value,
        reason=control.reason,
        actor=control.actor,
        policy_version=control.policy_version,
        effective_at=control.effective_at,
        recovery_condition=control.recovery_condition,
        version=control.version,
    )
