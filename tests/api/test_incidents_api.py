from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.application.incidents import IncidentService
from convictionos.domain.incidents import BreakerKind, IncidentSeverity
from convictionos.infrastructure.brokers import FakeBroker


async def test_incidents_api_lists_public_incidents_and_resolves_with_token(
    engine: AsyncEngine,
) -> None:
    service = IncidentService.for_engine(engine)
    incident = await service.open_breaker(
        workspace_id="workspace-001",
        account_id="paper-account",
        agent_id="convictionos",
        kind=BreakerKind.DATABASE_RUNTIME,
        severity=IncidentSeverity.CRITICAL,
        trigger_evidence_hash="e" * 64,
        reason="database runtime outage",
        opened_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        recovery_condition="database healthy",
    )
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent_control_token="control-secret",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/v1/workspaces/workspace-001/incidents")
        forbidden = await client.post(
            f"/v1/workspaces/workspace-001/incidents/{incident.incident_id}/resolve",
            json={"expected_version": incident.version, "recovery_evidence_hash": "f" * 64},
        )
        resolved = await client.post(
            f"/v1/workspaces/workspace-001/incidents/{incident.incident_id}/resolve",
            headers={"X-Agent-Control-Token": "control-secret"},
            json={"expected_version": incident.version, "recovery_evidence_hash": "f" * 64},
        )

    assert listed.status_code == 200
    assert listed.json()["incidents"][0]["public_id"] == incident.public_id
    assert "paper-account" not in listed.text
    assert forbidden.status_code == 403
    assert resolved.status_code == 200
    assert resolved.json()["state"] == "resolved"
