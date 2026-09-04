import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from convictionos.application.model_gateway import (
    IntelligenceRequest,
    IntelligenceUnavailable,
    ModelGateway,
)
from convictionos.domain.intelligence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceSourceType,
    GroundedThesis,
    ThesisDirection,
)
from convictionos.domain.trading import Horizon
from convictionos.infrastructure.model_claude import ClaudeModelGateway
from convictionos.infrastructure.model_gemini import GeminiModelGateway
from convictionos.infrastructure.model_openai import OpenAIModelGateway
from convictionos.infrastructure.model_openai_compatible import OpenAICompatibleModelGateway

OBSERVED = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def valid_thesis_payload() -> dict[str, object]:
    return GroundedThesis(
        direction=ThesisDirection.BULLISH,
        confidence=Decimal("0.82"),
        horizon=Horizon.SWING,
        catalyst="Source-backed repricing catalyst.",
        causal_mechanism="Demand expectations improve forward earnings.",
        beneficiaries=("SPY",),
        adversely_affected=(),
        invalidation="Close below the pre-catalyst range.",
        material_risks=("Macro reversal",),
        source_ids=("alpaca-news:42",),
        contradicting_source_ids=(),
    ).model_dump(mode="json")


def intelligence_request() -> IntelligenceRequest:
    item = EvidenceItem(
        source_id="alpaca-news:42",
        provider="alpaca_news",
        source_type=EvidenceSourceType.NEWS,
        published_at=OBSERVED - timedelta(minutes=15),
        observed_at=OBSERVED,
        headline="Headline",
        summary="A bounded point-in-time summary.",
        symbols=("SPY",),
    )
    return IntelligenceRequest(
        evidence=EvidenceBundle(
            underlying="SPY",
            observed_at=OBSERVED,
            price=Decimal("505"),
            previous_close=Decimal("500"),
            items=(item,),
            version="evidence-v1",
        ),
        prompt_version="grounded-thesis-v1",
        schema_version="grounded-thesis-schema-v1",
    )


def configured_gateway_and_mock(
    provider: str, thesis: dict[str, object]
) -> tuple[ModelGateway, respx.Route]:
    text = json.dumps(thesis)
    if provider == "openai":
        route = respx.post("https://api.openai.com/v1/responses").mock(
            return_value=httpx.Response(
                200,
                json={
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": text}]}
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                },
            )
        )
        return OpenAIModelGateway(model="test-model", api_key="secret"), route
    if provider == "gemini":
        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": text}]}}],
                    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
                },
            )
        )
        return GeminiModelGateway(model="test-model", api_key="secret"), route
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )
    )
    return ClaudeModelGateway(model="test-model", api_key="secret"), route


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini", "claude"])
@respx.mock
async def test_adapter_returns_same_grounded_thesis_contract(provider: str) -> None:
    gateway, route = configured_gateway_and_mock(provider, valid_thesis_payload())

    result = await gateway.analyze(intelligence_request())

    assert result.provider == provider
    assert result.thesis.direction.value == "bullish"
    assert result.thesis.source_ids == ("alpaca-news:42",)
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert "secret" not in result.model_dump_json()
    assert route.called
    body = json.loads(route.calls[0].request.content)
    if provider == "openai":
        assert body["text"]["format"]["type"] == "json_schema"
    elif provider == "gemini":
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert "responseJsonSchema" in body["generationConfig"]
    else:
        assert body["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
@respx.mock
async def test_provider_http_failure_is_typed(status: int) -> None:
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(status, json={"error": {"message": "redacted"}})
    )
    gateway = OpenAIModelGateway(model="test-model", api_key="secret")

    with pytest.raises(IntelligenceUnavailable, match="openai") as error:
        await gateway.analyze(intelligence_request())

    assert "redacted" not in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_provider_output_is_typed_failure() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "not-json"}]},
        )
    )
    gateway = ClaudeModelGateway(model="test-model", api_key="secret")

    with pytest.raises(IntelligenceUnavailable, match="schema"):
        await gateway.analyze(intelligence_request())


@pytest.mark.asyncio
@respx.mock
async def test_refusal_or_missing_text_is_typed_failure() -> None:
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(200, json={"output": []})
    )
    gateway = OpenAIModelGateway(model="test-model", api_key="secret")

    with pytest.raises(IntelligenceUnavailable, match="refusal"):
        await gateway.analyze(intelligence_request())


@pytest.mark.asyncio
@respx.mock
async def test_timeout_is_typed_without_secret() -> None:
    route = respx.post("https://api.openai.com/v1/responses").mock(
        side_effect=httpx.ReadTimeout("secret timeout")
    )
    gateway = OpenAIModelGateway(model="test-model", api_key="secret")

    with pytest.raises(IntelligenceUnavailable, match="timeout") as error:
        await gateway.analyze(intelligence_request())

    assert route.called
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_gateway_uses_chat_completions_and_parses_json() -> None:
    route = respx.post("https://api.tokenrouter.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(valid_thesis_payload())}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22},
            },
        )
    )
    gateway = OpenAICompatibleModelGateway(
        model="openai/gpt-5.5", api_key="tokenrouter-secret", base_url="https://api.tokenrouter.com/v1"
    )

    result = await gateway.analyze(intelligence_request())

    assert result.provider == "openai-compatible"
    assert result.thesis.direction is ThesisDirection.BULLISH
    assert result.input_tokens == 11
    assert result.output_tokens == 22
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer tokenrouter-secret"
    body = json.loads(request.content)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert body["max_tokens"] == 1200
    assert body["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_gateway_rejects_empty_chat_content() -> None:
    respx.post("https://api.tokenrouter.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
    )
    gateway = OpenAICompatibleModelGateway(
        model="model", api_key="tokenrouter-secret", base_url="https://api.tokenrouter.com/v1"
    )

    with pytest.raises(IntelligenceUnavailable, match="missing output"):
        await gateway.analyze(intelligence_request())


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_gateway_sends_strict_provider_controls() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(valid_thesis_payload())}}]},
        )
    )
    gateway = OpenAICompatibleModelGateway(
        model="anthropic/claude-sonnet-4.5",
        api_key="openrouter-secret",
        base_url="https://openrouter.ai/api/v1",
        provider_name="openrouter",
    )

    await gateway.analyze(intelligence_request())

    body = json.loads(route.calls[0].request.content)
    assert body["provider"] == {
        "require_parameters": True,
        "allow_fallbacks": True,
        "data_collection": "deny",
    }


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_gateway_excludes_reasoning_for_structured_thesis() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(valid_thesis_payload())}}]},
        )
    )
    gateway = OpenAICompatibleModelGateway(
        model="openrouter/auto",
        api_key="openrouter-secret",
        base_url="https://openrouter.ai/api/v1",
        provider_name="openrouter",
    )

    await gateway.analyze(intelligence_request())

    body = json.loads(route.calls[0].request.content)
    assert body["reasoning"] == {"exclude": True}
