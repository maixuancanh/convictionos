from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.infrastructure.brokers import FakeBroker


async def test_demo_executes_one_order_and_denies_mutation(engine: AsyncEngine) -> None:
    broker = FakeBroker(raise_after_accept=True)
    app = create_app(engine=engine, broker=broker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo/run",
            json={
                "long_option_symbol": "SPY280120C00500000",
                "short_option_symbol": "SPY280120C00510000",
                "limit_price": "1.25",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["policy_outcome"] == "allow"
        assert body["broker_status"] == "filled"
        assert len(body["receipt_hash"]) == 64

        repeated = await client.post(
            "/v1/demo/run",
            json={
                "long_option_symbol": "SPY280120C00500000",
                "short_option_symbol": "SPY280120C00510000",
                "limit_price": "1.25",
            },
        )
        assert repeated.json()["receipt_hash"] == body["receipt_hash"]

        denied = await client.post(
            "/v1/demo/evaluate-mutation",
            json={"quantity": "20", "limit_price": "1.25"},
        )
        assert denied.status_code == 200
        assert denied.json()["outcome"] == "deny"
        assert denied.json()["operation_hash_changed"] is True
        assert broker.submit_calls == 1
