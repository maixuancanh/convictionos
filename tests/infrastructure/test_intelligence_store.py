from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.domain.intelligence import (
    GroundedThesis,
    IntelligenceArtifact,
    ThesisDirection,
)
from convictionos.domain.trading import Horizon
from convictionos.infrastructure.store import Store


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


def make_artifact() -> IntelligenceArtifact:
    return IntelligenceArtifact(
        artifact_id="artifact-identity-1",
        identity_hash="identity-1",
        evidence_snapshot_hash="evidence-1",
        thesis=valid_thesis(),
        provider="openai",
        model="test-model",
        prompt_version="grounded-thesis-v1",
        schema_version="grounded-thesis-schema-v1",
        generated_at=datetime(2026, 8, 30, 15, 0, tzinfo=UTC),
        valid=True,
        validation_reasons=(),
    )


def store(engine: AsyncEngine) -> Store:
    return Store(async_sessionmaker(engine, expire_on_commit=False))


def test_artifact_is_immutable_and_hashes_payload() -> None:
    artifact = make_artifact()

    with pytest.raises(ValidationError):
        artifact.provider = "gemini"

    assert artifact.artifact_hash() == artifact.artifact_hash()
    changed = artifact.model_copy(update={"provider": "gemini"})
    assert changed.artifact_hash() != artifact.artifact_hash()


@pytest.mark.asyncio
async def test_create_artifact_reuses_identical_identity(engine: AsyncEngine) -> None:
    artifact = make_artifact()
    first = await store(engine).create_or_load_intelligence_artifact(artifact)
    second = await store(engine).create_or_load_intelligence_artifact(artifact)

    assert first == second == artifact
    assert await store(engine).find_intelligence_artifact(artifact.identity_hash) == artifact


@pytest.mark.asyncio
async def test_same_identity_cannot_change_payload(engine: AsyncEngine) -> None:
    artifact = make_artifact()
    await store(engine).create_or_load_intelligence_artifact(artifact)
    changed = artifact.model_copy(
        update={"thesis": artifact.thesis.model_copy(update={"confidence": Decimal("0.51")})}
    )

    with pytest.raises(ValueError, match="different artifact hash"):
        await store(engine).create_or_load_intelligence_artifact(changed)
