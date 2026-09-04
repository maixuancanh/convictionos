import re
from datetime import datetime, timedelta

import httpx

from convictionos.application.agent import AgentMarketFrame
from convictionos.domain.intelligence import EvidenceBundle, EvidenceItem, EvidenceSourceType


def normalize_text(value: str, maximum: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:maximum]


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AlpacaEvidenceGateway:
    def __init__(
        self,
        *,
        api_key_id: str = "",
        api_secret_key: str = "",
        oauth_token: str = "",
        data_base_url: str = "https://data.alpaca.markets",
    ) -> None:
        data_url = data_base_url.rstrip("/")
        if data_url != "https://data.alpaca.markets":
            raise ValueError("Alpaca data base_url must be https://data.alpaca.markets")
        if not oauth_token and not (api_key_id and api_secret_key):
            raise ValueError("Alpaca data credentials are required")
        headers = (
            {"Authorization": f"Bearer {oauth_token}"}
            if oauth_token
            else {
                "APCA-API-KEY-ID": api_key_id,
                "APCA-API-SECRET-KEY": api_secret_key,
            }
        )
        self._client = httpx.AsyncClient(base_url=data_url, headers=headers, timeout=10.0)

    async def news(
        self, underlying: str, *, observed_at: datetime, lookback_hours: int
    ) -> tuple[EvidenceItem, ...]:
        response = await self._client.get(
            "/v1beta1/news",
            params={
                "symbols": underlying,
                "start": (observed_at - timedelta(hours=lookback_hours)).isoformat(),
                "end": observed_at.isoformat(),
                "sort": "desc",
                "limit": 10,
                "include_content": "false",
            },
        )
        response.raise_for_status()
        values: list[EvidenceItem] = []
        for raw in response.json().get("news", []):
            published_at = parse_datetime(raw["created_at"])
            if published_at > observed_at:
                continue
            headline = normalize_text(str(raw["headline"]), 500)
            values.append(
                EvidenceItem(
                    source_id=f"alpaca-news:{raw['id']}",
                    provider="alpaca_news",
                    source_type=EvidenceSourceType.NEWS,
                    published_at=published_at,
                    observed_at=observed_at,
                    headline=headline,
                    summary=normalize_text(str(raw.get("summary") or headline), 4000),
                    symbols=tuple(sorted(set(raw.get("symbols", [])))),
                    url=str(raw.get("url", "")),
                )
            )
        return tuple(sorted(values, key=lambda value: value.source_id))

    @staticmethod
    def build_bundle(
        frame: AgentMarketFrame, news: tuple[EvidenceItem, ...]
    ) -> EvidenceBundle:
        market = EvidenceItem(
            source_id=f"alpaca-market:{frame.underlying}:{frame.observed_at.isoformat()}",
            provider="alpaca_market",
            source_type=EvidenceSourceType.MARKET,
            published_at=frame.observed_at,
            observed_at=frame.observed_at,
            headline=f"{frame.underlying} market snapshot",
            summary=normalize_text(
                (
                    f"Price {frame.price}; previous close {frame.previous_close}; "
                    f"source {frame.source}."
                ),
                4000,
            ),
            symbols=(frame.underlying,),
        )
        return EvidenceBundle(
            underlying=frame.underlying,
            observed_at=frame.observed_at,
            price=frame.price,
            previous_close=frame.previous_close,
            items=(market, *news),
            version="evidence-v1",
        )

    async def aclose(self) -> None:
        await self._client.aclose()
