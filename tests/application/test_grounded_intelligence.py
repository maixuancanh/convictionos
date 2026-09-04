from datetime import UTC, datetime, timedelta
from decimal import Decimal

from convictionos.application.grounded_intelligence import validate_thesis
from convictionos.application.model_gateway import (
    IntelligenceRequest,
    IntelligenceUnavailable,
    ModelGateway,
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

OBSERVED = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        underlying="SPY",
        observed_at=OBSERVED,
        price=Decimal("505"),
        previous_close=Decimal("500"),
        items=tuple(
            EvidenceItem(
                source_id=source_id,
                provider="test",
                source_type=EvidenceSourceType.NEWS,
                published_at=OBSERVED - timedelta(minutes=15),
                observed_at=OBSERVED,
                headline=f"Headline {source_id}",
                summary="A bounded point-in-time summary.",
                symbols=("SPY",),
            )
            for source_id in ("source-1", "source-2")
        ),
        version="evidence-v1",
    )


def make_thesis(**updates: object) -> GroundedThesis:
    values: dict[str, object] = {
        "direction": ThesisDirection.BULLISH,
        "confidence": Decimal("0.85"),
        "horizon": Horizon.SWING,
        "catalyst": "A supported catalyst.",
        "causal_mechanism": "A bounded causal mechanism.",
        "beneficiaries": ("SPY",),
        "adversely_affected": (),
        "invalidation": "Observable invalidation.",
        "material_risks": ("Counterargument",),
        "source_ids": ("source-1",),
        "contradicting_source_ids": (),
    }
    values.update(updates)
    return GroundedThesis(**values)


def test_provider_value_objects_are_frozen_and_protocol_is_available() -> None:
    request = IntelligenceRequest(
        evidence=make_bundle(), prompt_version="prompt-v1", schema_version="schema-v1"
    )
    result = ModelResult(
        thesis=make_thesis(), provider="test", model="model", input_tokens=10, output_tokens=20
    )
    assert request.schema_version == "schema-v1"
    assert result.input_tokens == 10
    assert issubclass(IntelligenceUnavailable, RuntimeError)
    assert ModelGateway is not None


def test_rejects_unknown_source_id() -> None:
    result = validate_thesis(
        make_thesis(source_ids=("invented-source",)), make_bundle(), Decimal("0.70")
    )
    assert result.valid is False
    assert result.reasons == ("unknown_source_id:invented-source",)


def test_returns_all_deterministic_validation_reasons() -> None:
    result = validate_thesis(
        make_thesis(
            confidence=Decimal("0.40"),
            beneficiaries=("QQQ",),
            source_ids=("source-1", "invented"),
            contradicting_source_ids=("source-1", "invented"),
        ),
        make_bundle(),
        Decimal("0.70"),
    )
    assert result.valid is False
    assert result.reasons == (
        "unknown_source_id:invented",
        "low_confidence",
        "bullish_underlying_not_beneficiary",
        "support_contradiction_overlap:source-1",
        "support_contradiction_overlap:invented",
    )


def test_rejects_bearish_and_neutral_directional_claims() -> None:
    bearish = validate_thesis(
        make_thesis(direction=ThesisDirection.BEARISH, beneficiaries=("SPY",)),
        make_bundle(),
        Decimal("0.70"),
    )
    neutral = validate_thesis(
        make_thesis(
            direction=ThesisDirection.NEUTRAL,
            beneficiaries=("SPY",),
            adversely_affected=("QQQ",),
        ),
        make_bundle(),
        Decimal("0.70"),
    )
    assert "bearish_underlying_not_adversely_affected" in bearish.reasons
    assert "neutral_directional_lists" in neutral.reasons


def test_accepts_fully_grounded_thesis() -> None:
    result = validate_thesis(make_thesis(), make_bundle(), Decimal("0.70"))
    assert result.valid is True
    assert result.reasons == ()
