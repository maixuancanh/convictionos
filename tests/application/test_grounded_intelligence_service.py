import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.application.grounded_intelligence import GroundedIntelligenceService
from convictionos.application.model_gateway import (
    IntelligenceRequest,
    IntelligenceUnavailable,
    ModelResult,
)
from convictionos.domain.intelligence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceSourceType,
    GroundedThesis,
    ThesisDirection,
)
from convictionos.domain.trading import Horizon
from convictionos.infrastructure.store import Store

OBSERVED = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


class CountingGateway:
    provider = "openai"
    model = "test-model"

    def __init__(self, result: ModelResult) -> None:
        self.result = result
        self.calls = 0

    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        del request
        self.calls += 1
        return self.result


class FailingGateway:
    provider = "openai"
    model = "test-model"

    def __init__(self, error: IntelligenceUnavailable) -> None:
        self.error = error

    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        del request
        raise self.error


class ConcurrentGateway(CountingGateway):
    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        await asyncio.sleep(0)
        return await super().analyze(request)


def valid_thesis() -> GroundedThesis:
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
        source_ids=("news-1",),
        contradicting_source_ids=(),
    )


def valid_model_result() -> ModelResult:
    return ModelResult(thesis=valid_thesis(), provider="openai", model="test-model")


def result_with_unknown_source() -> ModelResult:
    result = valid_model_result()
    return result.model_copy(
        update={"thesis": result.thesis.model_copy(update={"source_ids": ("invented",)})}
    )


def bundle() -> EvidenceBundle:
    return EvidenceBundle(
        underlying="SPY",
        observed_at=OBSERVED,
        price=Decimal("505"),
        previous_close=Decimal("500"),
        items=(
            EvidenceItem(
                source_id="news-1",
                provider="alpaca_news",
                source_type=EvidenceSourceType.NEWS,
                published_at=OBSERVED - timedelta(minutes=15),
                observed_at=OBSERVED,
                headline="Headline news-1",
                summary="A bounded point-in-time summary.",
                symbols=("SPY",),
                url="https://example.test/news-1",
            ),
        ),
        version="evidence-v1",
    )


def store(engine: AsyncEngine) -> Store:
    return Store(async_sessionmaker(engine, expire_on_commit=False))


@pytest.mark.asyncio
async def test_identical_request_calls_model_once(engine: AsyncEngine) -> None:
    gateway = CountingGateway(valid_model_result())
    service = GroundedIntelligenceService(store(engine), gateway)

    first = await service.analyze(bundle(), prompt_version="thesis-v1")
    second = await service.analyze(bundle(), prompt_version="thesis-v1")

    assert first == second
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_invalid_model_thesis_is_persisted_as_non_executable(
    engine: AsyncEngine,
) -> None:
    gateway = CountingGateway(result_with_unknown_source())
    artifact = await GroundedIntelligenceService(store(engine), gateway).analyze(
        bundle(), prompt_version="thesis-v1"
    )

    assert artifact.valid is False
    assert artifact.validation_reasons == ("unknown_source_id:invented",)
    assert await store(engine).find_intelligence_artifact(artifact.identity_hash) == artifact


@pytest.mark.asyncio
async def test_provider_failure_returns_typed_abstention_without_artifact(
    engine: AsyncEngine,
) -> None:
    gateway = FailingGateway(IntelligenceUnavailable("provider timeout"))
    result = await GroundedIntelligenceService(store(engine), gateway).try_analyze(
        bundle(), prompt_version="thesis-v1"
    )

    assert result.artifact is None
    assert result.abstention_reason == "intelligence_unavailable"


@pytest.mark.asyncio
async def test_try_analyze_propagates_programming_errors(engine: AsyncEngine) -> None:
    class BrokenGateway(CountingGateway):
        async def analyze(self, request: IntelligenceRequest) -> ModelResult:
            del request
            raise TypeError("programming error")

    with pytest.raises(TypeError, match="programming error"):
        await GroundedIntelligenceService(
            store(engine), BrokenGateway(valid_model_result())
        ).try_analyze(bundle(), prompt_version="thesis-v1")


@pytest.mark.asyncio
async def test_concurrent_identical_requests_store_one_immutable_artifact(
    engine: AsyncEngine,
) -> None:
    gateway = ConcurrentGateway(valid_model_result())
    service = GroundedIntelligenceService(store(engine), gateway)

    first, second = await asyncio.gather(
        service.analyze(bundle(), prompt_version="thesis-v1"),
        service.analyze(bundle(), prompt_version="thesis-v1"),
    )

    assert first == second
    assert first.artifact_hash() == second.artifact_hash()
    assert gateway.calls == 2
    with pytest.raises(ValidationError):
        first.provider = "gemini"
