from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.infrastructure.brokers import FakeBroker


async def test_landing_and_dashboard_are_paper_safe(engine: AsyncEngine) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        landing = await client.get("/")
        dashboard = await client.get("/dashboard")
        state = await client.get("/v1/control-plane")

    assert landing.status_code == 200
    assert "Move with conviction" in landing.text
    assert 'href="/dashboard"' in landing.text
    assert dashboard.status_code == 200
    assert "Mission Control" in dashboard.text
    assert state.json()["execution_mode"] == "FAKE DEMO · PAPER-SAFE"
    assert state.json()["live_trading_authorized"] is False


async def test_all_dashboard_pages_have_navigation_and_safety_disclosure(
    engine: AsyncEngine,
) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.get(route)
            for route in ("/", "/dashboard", "/decisions", "/research", "/proof")
        ]

    for response in responses:
        assert response.status_code == 200
        assert "<main" in response.text
        assert "PAPER-SAFE" in response.text
        for route in ("/", "/dashboard", "/decisions", "/research", "/proof"):
            assert f'href="{route}"' in response.text


async def test_control_plane_returns_typed_json_contract(engine: AsyncEngine) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/control-plane")

    assert response.status_code == 200
    body = response.json()
    assert body["synthetic"] is True
    assert body["latest_decision"] is None
    assert body["evidence_source_id"].startswith("fixture://")
    assert body["lifecycle"] == {
        "open": 0,
        "close_pending": 0,
        "review": 0,
        "closed": 0,
        "positions": [],
    }


async def test_dashboard_serves_local_assets_and_safe_form(engine: AsyncEngine) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dashboard = await client.get("/dashboard")
        css = await client.get("/static/dashboard.css")
        javascript = await client.get("/static/dashboard.js")

    assert 'action="/v1/demo/evaluate-mutation"' not in dashboard.text
    assert 'id="mutation-form"' in dashboard.text
    assert "Control token" in dashboard.text
    assert 'data-scheduler-action="run-now"' in dashboard.text
    assert 'data-lifecycle-count="open"' in dashboard.text
    assert 'id="managed-positions-list"' in dashboard.text
    assert css.status_code == 200
    assert "--safe" in css.text
    assert javascript.status_code == 200
    assert "/v1/demo/evaluate-mutation" in javascript.text
    assert "/v1/demo/run" not in javascript.text
    assert "/v1/positions" in javascript.text


async def test_public_positions_projection_contains_no_private_identifiers(
    engine: AsyncEngine,
) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/positions")
        dashboard = await client.get("/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["positions"] == []
    assert set(body) == {"open", "close_pending", "review", "closed", "positions"}
    assert "Managed Positions" in dashboard.text
    for private in ("account_id", "broker_order_id", "intent_id", "activity_id", "secret"):
        assert private not in response.text
        assert private not in dashboard.text
