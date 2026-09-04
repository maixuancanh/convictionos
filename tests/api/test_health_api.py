from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.infrastructure.brokers import FakeBroker


async def test_health_live_and_ready_expose_safe_release_state(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        runtime_release_sha="f" * 40,
        cli_revision="cli-revision",
        mcp_version="2.3.1",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {
        "status": "ok",
        "release_sha": "ffffffffffffffffffffffffffffffffffffffff",
    }
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migration"] in {"ok", "unversioned"}
    assert body["release_sha"] == "ffffffffffffffffffffffffffffffffffffffff"
    assert "secret" not in ready.text


async def test_metrics_route_uses_bounded_safe_labels(engine: AsyncEngine) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        runtime_release_sha="f" * 40,
        cli_revision="cli-revision",
        mcp_version="2.3.1",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert "convictionos_release_info" in response.text
    assert "client_order_id" not in response.text
    assert "cli-revision" not in response.text


async def test_public_readiness_allows_vercel_dashboard_origin(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        runtime_release_sha="f" * 40,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/public/competition-readiness",
            headers={"Origin": "https://convictionos-web.vercel.app"},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "https://convictionos-web.vercel.app"
    )


async def test_public_readiness_reports_release_metadata_without_manifest(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        runtime_release_sha="f" * 40,
        cli_revision="cli-revision",
        mcp_version="2.3.1",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/public/competition-readiness")

    body = response.json()
    assert body["outcome"] == "unavailable"
    assert body["deployed_sha"] == "f" * 40
    assert body["cli_revision"] == "cli-revision"
    assert body["mcp_version"] == "2.3.1"
