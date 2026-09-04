from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from convictionos.domain.incidents import (
    BreakerKind,
    IncidentSeverity,
    IncidentState,
    RuntimeIncident,
)
from convictionos.domain.runtime import RuntimeControl, RuntimeControlState, RuntimeScopeType
from convictionos.infrastructure.db import build_session_factory
from convictionos.infrastructure.models import (
    RuntimeControlRow,
    RuntimeIncidentEventRow,
    RuntimeIncidentRow,
)
from convictionos.infrastructure.runtime_store import RuntimeStore
from convictionos.observability import Redactor


class StaleIncidentVersion(RuntimeError):
    pass


class IncidentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime_store: RuntimeStore,
    ) -> None:
        self._sessions = sessions
        self._runtime_store = runtime_store

    @classmethod
    def for_engine(cls, engine: AsyncEngine) -> IncidentService:
        sessions = build_session_factory(engine)
        return cls(sessions, RuntimeStore(sessions))

    async def open_breaker(
        self,
        *,
        workspace_id: str,
        account_id: str,
        agent_id: str,
        kind: BreakerKind,
        severity: IncidentSeverity,
        trigger_evidence_hash: str,
        reason: str,
        opened_at: datetime,
        recovery_condition: str,
    ) -> RuntimeIncident:
        incident_id = f"incident-{uuid4().hex}"
        control = RuntimeControl(
            control_id=f"control-{incident_id}",
            scope_type=RuntimeScopeType.ACCOUNT,
            scope_id=account_id,
            state=_control_state_for(severity),
            reason=_safe_reason(reason),
            actor="system",
            policy_version="runtime-policy-v1",
            effective_at=opened_at,
            recovery_condition=recovery_condition,
            version=1,
        )
        await self._runtime_store.put_control(control)
        incident = RuntimeIncident(
            incident_id=incident_id,
            public_id=f"inc_{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            account_id=account_id,
            agent_id=agent_id,
            kind=kind,
            severity=severity,
            state=IncidentState.OPEN,
            trigger_evidence_hash=trigger_evidence_hash,
            policy_version="runtime-policy-v1",
            reason=_safe_reason(reason),
            recovery_condition=recovery_condition,
            opened_at=opened_at,
            version=1,
        )
        async with self._sessions() as session:
            session.add(_incident_row(incident, runtime_control_id=control.control_id))
            await session.flush()
            session.add(
                RuntimeIncidentEventRow(
                    event_id=f"incident-event-{uuid4().hex}",
                    incident_id=incident.incident_id,
                    actor="system",
                    event_type="opened",
                    evidence_hash=trigger_evidence_hash,
                    observed_at=opened_at,
                )
            )
            await session.commit()
        return incident

    async def resolve(
        self,
        incident_id: str,
        *,
        actor: str,
        expected_version: int,
        resolved_at: datetime,
        recovery_evidence_hash: str | None,
    ) -> RuntimeIncident:
        if recovery_evidence_hash is None:
            raise ValueError("recovery evidence is required")
        async with self._sessions() as session:
            row = await session.get(RuntimeIncidentRow, incident_id, with_for_update=True)
            if row is None:
                raise KeyError(incident_id)
            if row.version != expected_version:
                raise StaleIncidentVersion("incident version is stale")
            row.state = IncidentState.RESOLVED.value
            row.resolved_by = actor
            row.recovery_evidence_hash = recovery_evidence_hash
            row.version += 1
            session.add(
                RuntimeIncidentEventRow(
                    event_id=f"incident-event-{uuid4().hex}",
                    incident_id=incident_id,
                    actor=actor,
                    event_type="resolved",
                    evidence_hash=recovery_evidence_hash,
                    observed_at=resolved_at,
                )
            )
            await session.commit()
            return _incident(row)

    async def runtime_control_for_incident(
        self, incident_id: str
    ) -> RuntimeControl | None:
        async with self._sessions() as session:
            row = await session.get(RuntimeIncidentRow, incident_id)
            if row is None or row.runtime_control_id is None:
                return None
            control_row = await session.get(RuntimeControlRow, row.runtime_control_id)
            if control_row is None:
                return None
            return RuntimeControl(
                control_id=control_row.control_id,
                scope_type=RuntimeScopeType(control_row.scope_type),
                scope_id=control_row.scope_id,
                state=RuntimeControlState(control_row.state),
                reason=control_row.reason,
                actor=control_row.actor,
                policy_version=control_row.policy_version,
                effective_at=control_row.effective_at,
                recovery_condition=control_row.recovery_condition,
                version=control_row.version,
            )

    async def list_public(self, workspace_id: str) -> tuple[dict[str, object], ...]:
        redactor = Redactor(secret_values=())
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(RuntimeIncidentRow)
                    .where(RuntimeIncidentRow.workspace_id == workspace_id)
                    .order_by(RuntimeIncidentRow.opened_at.desc())
                )
            ).all()
        return tuple(
            {
                "public_id": row.public_id,
                "kind": row.kind,
                "severity": row.severity,
                "state": row.state,
                "reason": redactor.clean(_safe_reason(row.reason)),
                "opened_at": row.opened_at.isoformat(),
                "recovery_condition": row.recovery_condition,
                "version": row.version,
            }
            for row in rows
        )


def _control_state_for(severity: IncidentSeverity) -> RuntimeControlState:
    if severity is IncidentSeverity.CRITICAL:
        return RuntimeControlState.CLOSING_ONLY
    if severity in {IncidentSeverity.ERROR, IncidentSeverity.WARNING}:
        return RuntimeControlState.PAUSED
    return RuntimeControlState.RUNNING


_SECRET_ASSIGNMENT = re.compile(r"(?i)(token|secret|password|authorization)=[^\s;]+")


def _safe_reason(reason: str) -> str:
    return _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", reason)


def _incident_row(
    incident: RuntimeIncident, *, runtime_control_id: str | None
) -> RuntimeIncidentRow:
    return RuntimeIncidentRow(
        incident_id=incident.incident_id,
        public_id=incident.public_id,
        workspace_id=incident.workspace_id,
        account_id=incident.account_id,
        agent_id=incident.agent_id,
        kind=incident.kind.value,
        severity=incident.severity.value,
        state=incident.state.value,
        trigger_evidence_hash=incident.trigger_evidence_hash,
        policy_version=incident.policy_version,
        reason=incident.reason,
        recovery_condition=incident.recovery_condition,
        opened_at=incident.opened_at,
        acknowledged_by=incident.acknowledged_by,
        resolved_by=incident.resolved_by,
        recovery_evidence_hash=incident.recovery_evidence_hash,
        version=incident.version,
        runtime_control_id=runtime_control_id,
    )


def _incident(row: RuntimeIncidentRow) -> RuntimeIncident:
    return RuntimeIncident(
        incident_id=row.incident_id,
        public_id=row.public_id,
        workspace_id=row.workspace_id,
        account_id=row.account_id,
        agent_id=row.agent_id,
        kind=BreakerKind(row.kind),
        severity=IncidentSeverity(row.severity),
        state=IncidentState(row.state),
        trigger_evidence_hash=row.trigger_evidence_hash,
        policy_version=row.policy_version,
        reason=row.reason,
        recovery_condition=row.recovery_condition,
        opened_at=row.opened_at,
        acknowledged_by=row.acknowledged_by,
        resolved_by=row.resolved_by,
        recovery_evidence_hash=row.recovery_evidence_hash,
        version=row.version,
    )
