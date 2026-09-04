from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.domain.intelligence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceSourceType,
    GroundedThesis,
    ThesisDirection,
)
from convictionos.domain.trading import Horizon

OBSERVED = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def item(source_id: str, *, published_at: datetime | None = None) -> EvidenceItem:
    return EvidenceItem(
        source_id=source_id,
        provider="alpaca_news",
        source_type=EvidenceSourceType.NEWS,
        published_at=published_at or OBSERVED - timedelta(minutes=15),
        observed_at=OBSERVED,
        headline=f"Headline {source_id}",
        summary="A bounded point-in-time summary.",
        symbols=("SPY",),
        url=f"https://example.test/{source_id}",
    )


def test_bundle_hash_is_order_independent() -> None:
    first = EvidenceBundle(
        underlying="SPY",
        observed_at=OBSERVED,
        price=Decimal("505"),
        previous_close=Decimal("500"),
        items=(item("news-2"), item("news-1")),
        version="evidence-v1",
    )
    second = first.model_copy(update={"items": tuple(reversed(first.items))})
    assert first.snapshot_hash() == second.snapshot_hash()


def test_legacy_bundle_hash_is_preserved_when_market_truth_is_absent() -> None:
    legacy = EvidenceBundle(
        underlying="SPY",
        observed_at=OBSERVED,
        price=Decimal("505"),
        previous_close=Decimal("500"),
        items=(item("news-1"),),
        version="evidence-v1",
    )
    explicit_absent = legacy.model_copy(
        update={"market_truth_hash": None, "mcp_observation_hashes": ()}
    )

    assert explicit_absent.snapshot_hash() == legacy.snapshot_hash()


def test_grounded_options_router_v2_requires_market_truth_hashes() -> None:
    with pytest.raises(ValidationError, match="market truth"):
        EvidenceBundle(
            underlying="SPY",
            observed_at=OBSERVED,
            price=Decimal("505"),
            previous_close=Decimal("500"),
            items=(item("news-1"),),
            version="grounded-options-router-v2",
        )

    bundle = EvidenceBundle(
        underlying="SPY",
        observed_at=OBSERVED,
        price=Decimal("505"),
        previous_close=Decimal("500"),
        items=(item("news-1"),),
        version="grounded-options-router-v2",
        market_truth_hash="truth-hash",
        mcp_observation_hashes=("mcp-hash",),
    )

    assert bundle.market_truth_hash == "truth-hash"
    assert bundle.mcp_observation_hashes == ("mcp-hash",)


def test_bundle_rejects_future_evidence() -> None:
    with pytest.raises(ValidationError, match="future evidence"):
        EvidenceBundle(
            underlying="SPY",
            observed_at=OBSERVED,
            price=Decimal("505"),
            previous_close=Decimal("500"),
            items=(item("future", published_at=OBSERVED + timedelta(seconds=1)),),
            version="evidence-v1",
        )


def test_bundle_rejects_duplicate_source_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate evidence source_id"):
        EvidenceBundle(
            underlying="SPY",
            observed_at=OBSERVED,
            price=Decimal("505"),
            previous_close=Decimal("500"),
            items=(item("same"), item("same")),
            version="evidence-v1",
        )


def test_grounded_thesis_excludes_execution_fields() -> None:
    thesis = GroundedThesis(
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
    assert thesis.direction is ThesisDirection.BULLISH
    assert "quantity" not in GroundedThesis.model_fields
    assert "option_symbol" not in GroundedThesis.model_fields


def test_grounded_thesis_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        GroundedThesis(
            direction=ThesisDirection.NEUTRAL,
            confidence=Decimal("0.5"),
            horizon=Horizon.SWING,
            catalyst="Catalyst.",
            causal_mechanism="Mechanism.",
            beneficiaries=(),
            adversely_affected=(),
            invalidation="Invalidation.",
            material_risks=(),
            source_ids=("news-1",),
            contradicting_source_ids=(),
            quantity=1,
        )
