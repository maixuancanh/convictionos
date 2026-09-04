from datetime import UTC
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from convictionos.application.model_gateway import (
    IntelligenceRequest,
    IntelligenceUnavailable,
    ModelGateway,
)
from convictionos.domain.canonical import sha256_hex
from convictionos.domain.intelligence import (
    EvidenceBundle,
    GroundedThesis,
    IntelligenceArtifact,
    ThesisDirection,
)
from convictionos.infrastructure.store import Store

SCHEMA_VERSION = "grounded-thesis-schema-v1"
DEFAULT_MINIMUM_CONFIDENCE = Decimal("0.70")


class ThesisValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    reasons: tuple[str, ...]


class GroundedIntelligenceResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact: IntelligenceArtifact | None = None
    abstention_reason: str | None = None
    validation_reasons: tuple[str, ...] = ()


class GroundedIntelligenceService:
    def __init__(self, store: Store, gateway: ModelGateway) -> None:
        self._store = store
        self._gateway = gateway

    async def analyze(
        self,
        bundle: EvidenceBundle,
        *,
        prompt_version: str,
        minimum_confidence: Decimal = DEFAULT_MINIMUM_CONFIDENCE,
    ) -> IntelligenceArtifact:
        identity_hash = sha256_hex(
            {
                "evidence": bundle.snapshot_hash(),
                "provider": self._gateway.provider,
                "model": self._gateway.model,
                "prompt_version": prompt_version,
                "schema_version": SCHEMA_VERSION,
            }
        )
        existing = await self._store.find_intelligence_artifact(identity_hash)
        if existing is not None:
            return existing

        result = await self._gateway.analyze(
            IntelligenceRequest(
                evidence=bundle,
                prompt_version=prompt_version,
                schema_version=SCHEMA_VERSION,
            )
        )
        validation = validate_thesis(
            result.thesis, bundle, minimum_confidence=minimum_confidence
        )
        artifact = IntelligenceArtifact(
            artifact_id=f"intelligence-{identity_hash}",
            identity_hash=identity_hash,
            evidence_snapshot_hash=bundle.snapshot_hash(),
            thesis=result.thesis,
            provider=result.provider,
            model=result.model,
            prompt_version=prompt_version,
            schema_version=SCHEMA_VERSION,
            generated_at=bundle.observed_at.astimezone(UTC),
            valid=validation.valid,
            validation_reasons=validation.reasons,
        )
        return await self._store.create_or_load_intelligence_artifact(artifact)

    async def try_analyze(
        self,
        bundle: EvidenceBundle,
        *,
        prompt_version: str,
        minimum_confidence: Decimal = DEFAULT_MINIMUM_CONFIDENCE,
    ) -> GroundedIntelligenceResult:
        try:
            artifact = await self.analyze(
                bundle,
                prompt_version=prompt_version,
                minimum_confidence=minimum_confidence,
            )
        except IntelligenceUnavailable:
            return GroundedIntelligenceResult(
                abstention_reason="intelligence_unavailable"
            )
        if not artifact.valid:
            return GroundedIntelligenceResult(
                artifact=artifact,
                abstention_reason="grounded_thesis_invalid",
                validation_reasons=artifact.validation_reasons,
            )
        return GroundedIntelligenceResult(artifact=artifact)


def validate_thesis(
    thesis: GroundedThesis,
    bundle: EvidenceBundle,
    minimum_confidence: Decimal,
) -> ThesisValidation:
    reasons: list[str] = []
    source_ids = {item.source_id for item in bundle.items}

    for source_id in (*thesis.source_ids, *thesis.contradicting_source_ids):
        if source_id not in source_ids and f"unknown_source_id:{source_id}" not in reasons:
            reasons.append(f"unknown_source_id:{source_id}")
    if thesis.confidence < minimum_confidence:
        reasons.append("low_confidence")
    if (
        thesis.direction is ThesisDirection.BULLISH
        and bundle.underlying not in thesis.beneficiaries
    ):
        reasons.append("bullish_underlying_not_beneficiary")
    if (
        thesis.direction is ThesisDirection.BEARISH
        and bundle.underlying not in thesis.adversely_affected
    ):
        reasons.append("bearish_underlying_not_adversely_affected")
    for source_id in (*thesis.source_ids, *thesis.contradicting_source_ids):
        if (
            source_id in thesis.source_ids
            and source_id in thesis.contradicting_source_ids
            and f"support_contradiction_overlap:{source_id}" not in reasons
        ):
            reasons.append(f"support_contradiction_overlap:{source_id}")
    if thesis.direction is ThesisDirection.NEUTRAL and (
        thesis.beneficiaries or thesis.adversely_affected
    ):
        reasons.append("neutral_directional_lists")
    return ThesisValidation(valid=not reasons, reasons=tuple(reasons))
