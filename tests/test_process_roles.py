import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

os.environ["AGENT_ENABLED"] = "false"

from convictionos.api.main import build_application, build_container
from convictionos.commands import main, sanitized_startup_summary
from convictionos.settings import Settings
from convictionos.worker import build_worker_runtime
from convictionos import worker as worker_module


def paper_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "agent_enabled": True,
        "agent_control_token": "control-secret",
        "broker_mode": "alpaca_paper",
        "competition_account_id": "paper-account",
        "alpaca_cli_revision": "alpaca-cli-revision",
        "alpaca_cli_path": "tools/alpaca-cli",
        "alpaca_mcp_version": "alpaca-mcp-version",
        "alpaca_mcp_command": "uvx alpaca-mcp",
        "alpaca_mcp_schema_hash": "sha256:alpaca-mcp-schema",
        "ai_provider": "openrouter",
        "ai_model": "openrouter/auto",
        "ai_api_key": "openrouter-secret",
        "alpaca_api_key_id": "alpaca-key",
        "alpaca_api_secret_key": "alpaca-secret",
        "competition_workspace_id": "workspace-001",
        "runtime_agent_id": "convictionos",
        "runtime_release_sha": "f" * 40,
        "runtime_instance_id": "worker-a",
    }
    values.update(overrides)
    return Settings(**values)


def test_api_role_does_not_start_scheduler_background(engine: AsyncEngine) -> None:
    settings = paper_settings(database_url="postgresql+psycopg://unused/unused")

    app = build_application(settings, scheduler_background=False, engine=engine)

    assert app.router.lifespan_context is not None
    assert app.state.scheduler_background is False


def test_worker_role_builds_scheduler_with_runtime_coordinator(engine: AsyncEngine) -> None:
    settings = paper_settings(database_url="postgresql+psycopg://unused/unused")
    container = build_container(settings, engine=engine)

    runtime = build_worker_runtime(container)

    assert runtime.scheduler is not None
    assert runtime.scheduler._runtime_coordinator is not None
    assert runtime.release_sha == "f" * 40


def test_startup_summary_redacts_credentials() -> None:
    settings = paper_settings()

    summary = sanitized_startup_summary(settings, role="worker")

    rendered = str(summary)
    assert summary["role"] == "worker"
    assert summary["broker_mode"] == "alpaca_paper"
    assert "openrouter-secret" not in rendered
    assert "alpaca-secret" not in rendered
    assert "control-secret" not in rendered


async def test_worker_lease_renewer_refreshes_runtime_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moments = [
        datetime(2026, 9, 4, 14, 0, 30, tzinfo=UTC),
        datetime(2026, 9, 4, 14, 1, 0, tzinfo=UTC),
    ]
    sleeps: list[float] = []

    class FakeRuntimeCoordinator:
        def __init__(self) -> None:
            self.renewed: list[datetime] = []

        async def renew(self, *, now: datetime) -> None:
            self.renewed.append(now)
            if len(self.renewed) == len(moments):
                raise asyncio.CancelledError

    async def fast_sleep(interval_seconds: float) -> None:
        sleeps.append(interval_seconds)

    import asyncio

    runtime = FakeRuntimeCoordinator()
    monkeypatch.setattr(worker_module.asyncio, "sleep", fast_sleep)

    with pytest.raises(asyncio.CancelledError):
        await worker_module.renew_runtime_lease_forever(
            runtime,  # type: ignore[arg-type]
            interval_seconds=30.0,
            now_factory=lambda: moments[len(runtime.renewed)],
        )

    assert sleeps == [30.0, 30.0]
    assert runtime.renewed == moments


def test_api_role_allows_partial_worker_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: object, *, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setenv("BROKER_MODE", "alpaca_paper")
    monkeypatch.setenv("AGENT_CONTROL_TOKEN", "control-secret")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("AI_MODEL", "openrouter/auto")
    monkeypatch.setenv("AI_API_KEY", "openrouter-secret")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "paper-secret")
    monkeypatch.delenv("COMPETITION_ACCOUNT_ID", raising=False)
    monkeypatch.setattr("convictionos.commands.uvicorn.run", fake_run)

    main(["api", "--host", "127.0.0.1", "--port", "8123"])

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
