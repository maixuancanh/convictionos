from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.application.incidents import IncidentService, StaleIncidentVersion
from convictionos.domain.incidents import BreakerKind, IncidentSeverity, IncidentState
from convictionos.domain.runtime import RuntimeControlState


@pytest.mark.asyncio
async def test_opening_financial_incident_creates_closing_only_runtime_control(
    engine: AsyncEngine,
) -> None:
    service = IncidentService.for_engine(engine)
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)

    incident = await service.open_breaker(
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        kind=BreakerKind.BROKER_RECONCILIATION,
        severity=IncidentSeverity.CRITICAL,
        trigger_evidence_hash="a" * 64,
        reason="ACK_UNKNOWN exceeded review threshold",
        opened_at=now,
        recovery_condition="broker state reconciled",
    )
    control = await service.runtime_control_for_incident(incident.incident_id)

    assert incident.state is IncidentState.OPEN
    assert control is not None
    assert control.state is RuntimeControlState.CLOSING_ONLY
    assert control.reason == "ACK_UNKNOWN exceeded review threshold"


@pytest.mark.asyncio
async def test_resolving_incident_requires_recovery_evidence_and_current_version(
    engine: AsyncEngine,
) -> None:
    service = IncidentService.for_engine(engine)
    opened = await service.open_breaker(
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        kind=BreakerKind.AI_MCP_DEGRADATION,
        severity=IncidentSeverity.WARNING,
        trigger_evidence_hash="b" * 64,
        reason="MCP unavailable",
        opened_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        recovery_condition="MCP schema observed again",
    )

    with pytest.raises(ValueError, match="recovery evidence"):
        await service.resolve(
            opened.incident_id,
            actor="operator",
            expected_version=opened.version,
            resolved_at=datetime(2026, 9, 4, 14, 5, tzinfo=UTC),
            recovery_evidence_hash=None,
        )
    with pytest.raises(StaleIncidentVersion):
        await service.resolve(
            opened.incident_id,
            actor="operator",
            expected_version=opened.version + 1,
            resolved_at=datetime(2026, 9, 4, 14, 5, tzinfo=UTC),
            recovery_evidence_hash="c" * 64,
        )

    resolved = await service.resolve(
        opened.incident_id,
        actor="operator",
        expected_version=opened.version,
        resolved_at=datetime(2026, 9, 4, 14, 5, tzinfo=UTC),
        recovery_evidence_hash="c" * 64,
    )

    assert resolved.state is IncidentState.RESOLVED
    assert resolved.version == opened.version + 1
    assert resolved.resolved_by == "operator"


@pytest.mark.asyncio
async def test_incident_listing_uses_public_ids_without_secret_payloads(
    engine: AsyncEngine,
) -> None:
    service = IncidentService.for_engine(engine)
    await service.open_breaker(
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        kind=BreakerKind.MARKET_DATA,
        severity=IncidentSeverity.ERROR,
        trigger_evidence_hash="d" * 64,
        reason="option quotes stale; token=secret-value",
        opened_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        recovery_condition="fresh OPRA quote observed",
    )

    incidents = await service.list_public("workspace-001")

    assert len(incidents) == 1
    assert incidents[0]["public_id"].startswith("inc_")
    assert "paper-account" not in str(incidents[0])
    assert "secret-value" not in str(incidents[0])
