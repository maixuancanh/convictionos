import asyncio

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        response = await client.post(
            "/v1/demo/run",
            json={
                "long_option_symbol": "SPY280120C00500000",
                "short_option_symbol": "SPY280120C00510000",
                "limit_price": "1.25",
            },
        )
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
