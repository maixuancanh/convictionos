from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.infrastructure.brokers import FakeBroker


async def test_landing_to_proof_journey_stays_paper_safe(engine: AsyncEngine) -> None:
    app = create_app(engine=engine, broker=FakeBroker())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for route, expected in (
            ("/", "Open Mission Control"),
            ("/dashboard", "PAPER-SAFE"),
            ("/decisions", "No reconciled decision yet"),
            ("/research", "Synthetic evidence"),
            ("/proof", "No receipt to verify yet"),
        ):
            response = await client.get(route)
            assert response.status_code == 200
            assert expected in response.text
