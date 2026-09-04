from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.application.agent import AgentAction, AgentDecision, AgentRunResult, AgentRunState
from convictionos.domain.runtime import RuntimeControlState, RuntimeStatus
from convictionos.infrastructure.brokers import FakeBroker


class StubAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self, *, now: datetime) -> AgentRunResult:
        self.calls += 1
        return AgentRunResult(
            state=AgentRunState.ABSTAINED,
            decision=AgentDecision(
                decision_id="decision-1",
                action=AgentAction.ABSTAIN,
                underlying="SPY",
                thesis="No trade.",
                invalidation="Observe again.",
                reasons=("signal threshold was not met",),
                snapshot_hash="snapshot-1",
            ),
        )


class StubRuntimeCoordinator:
    async def status(self) -> RuntimeStatus:
        now = datetime.now(UTC)
        return RuntimeStatus(
            lease_name="agent:workspace-1:paper-account-secret:convictionos",
            lease_owner_instance_id="worker-a",
            fencing_token=7,
            lease_expires_at=now + timedelta(seconds=60),
            last_heartbeat_at=now - timedelta(seconds=10),
            last_cycle_state="reconciliation_only",
            effective_control_state=RuntimeControlState.RUNNING,
        )


async def test_agent_status_reports_disabled_by_default(engine: AsyncEngine) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status = await client.get("/v1/agent/status")
        run = await client.post("/v1/agent/run-once")

    assert status.json() == {
        "enabled": False,
        "configured": False,
        "broker_mode": "fake",
        "live_trading_authorized": False,
        "scheduler_running": False,
        "scheduler_paused": False,
        "last_run_at": None,
        "last_run_state": None,
        "runs": [],
        "eligibility": None,
        "last_observation_at": None,
        "next_cycle_at": None,
        "runtime_release_sha": "unknown",
        "cli_revision": None,
        "mcp_version": None,
        "degraded_reasons": [],
    }
    assert run.status_code == 503


async def test_run_once_requires_server_control_token(engine: AsyncEngine) -> None:
    agent = StubAgent()
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        broker_mode="alpaca_paper",
        agent=agent,
        agent_enabled=True,
        agent_control_token="control-secret",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/v1/agent/run-once")
        accepted = await client.post(
            "/v1/agent/run-once", headers={"X-Agent-Control-Token": "control-secret"}
        )
        dashboard = await client.get("/dashboard")

    assert missing.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "abstained"
    assert accepted.json()["decision"]["reasons"] == ["signal threshold was not met"]
    assert "AUTONOMOUS AGENT" in dashboard.text
    assert "READY · PAPER ONLY" in dashboard.text
    assert agent.calls == 1


async def test_scheduler_controls_pause_and_run_now(engine: AsyncEngine) -> None:
    agent = StubAgent()
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        broker_mode="alpaca_paper",
        agent=agent,
        agent_enabled=True,
        agent_control_token="control-secret",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        paused = await client.post(
            "/v1/agent/scheduler/pause",
            headers={"X-Agent-Control-Token": "control-secret"},
        )
        blocked = await client.post(
            "/v1/agent/scheduler/run-now",
            headers={"X-Agent-Control-Token": "control-secret"},
        )
        resumed = await client.post(
            "/v1/agent/scheduler/resume",
            headers={"X-Agent-Control-Token": "control-secret"},
        )
        accepted = await client.post(
            "/v1/agent/scheduler/run-now",
            headers={"X-Agent-Control-Token": "control-secret"},
        )

    assert paused.status_code == 200
    assert paused.json()["scheduler_paused"] is True
    assert blocked.status_code == 409
    assert resumed.json()["scheduler_paused"] is False
    assert accepted.status_code == 200
    assert agent.calls == 1


async def test_agent_status_projects_safe_runtime_metadata(engine: AsyncEngine) -> None:
    agent = StubAgent()
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        broker_mode="alpaca_paper",
        agent=agent,
        agent_enabled=True,
        agent_control_token="control-secret",
        runtime_release_sha="release-sha",
        cli_revision="cli-revision",
        mcp_version="alpaca-mcp-server==2.3.1",
        degraded_reasons=("MCP schema is required",),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status = await client.get("/v1/agent/status")

    body = status.json()
    assert body["runtime_release_sha"] == "release-sha"
    assert body["cli_revision"] == "cli-revision"
    assert body["mcp_version"] == "alpaca-mcp-server==2.3.1"
    assert body["degraded_reasons"] == ["MCP schema is required"]
    assert "control-secret" not in status.text
    assert "alpaca-secret" not in status.text


async def test_runtime_status_reports_worker_without_private_identifiers(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        runtime_coordinator=StubRuntimeCoordinator(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status = await client.get("/v1/runtime/status")

    assert status.status_code == 200
    body = status.json()
    assert body["configured"] is True
    assert body["worker_live"] is True
    assert body["lease_owner_instance_id"] == "worker-a"
    assert body["last_cycle_state"] == "reconciliation_only"
    assert body["effective_control_state"] == "running"
    assert "paper-account-secret" not in status.text
    assert "agent:workspace-1" not in status.text
    assert "fencing_token" not in status.text
