from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.market_data import (
    ExecutionTier,
    MarketDataCapability,
    MarketDataLimitation,
    OptionsFeed,
    UnderlyingFeed,
)
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.positions import (
    ManagedOptionPosition,
    PositionLifecycleState,
    PositionTransition,
)
from convictionos.domain.trading import (
    Horizon,
    InstrumentKind,
    PositionIntent,
    Side,
    TradeLeg,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
LONG_SYMBOL = "SPY280120C00500000"
SHORT_SYMBOL = "SPY280120C00510000"
LONG_PUT_SYMBOL = "SPY280120P00510000"
SHORT_PUT_SYMBOL = "SPY280120P00500000"


def accounting(*, broker_cost: str = "100") -> PaperExecutionAccounting:
    capability = MarketDataCapability(
        underlying_feed=UnderlyingFeed.IEX,
        options_feed=OptionsFeed.OPRA,
        execution_tier=ExecutionTier.PAPER_EXECUTABLE,
        limitations=(MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,),
        assessed_at=NOW,
    )
    return PaperExecutionAccounting(
        capability=capability,
        authorized_limit_price=Decimal("1.25"),
        spread_units=1,
        authorized_cost_ceiling=Decimal("125"),
        broker_reported_fill_price=Decimal("1"),
        broker_reported_filled_qty=Decimal("1"),
        broker_reported_cost_basis=Decimal(broker_cost),
        limitations=capability.limitations,
    )


def leg(symbol: str, side: Side, intent: PositionIntent, ratio: int = 1) -> TradeLeg:
    return TradeLeg(
        symbol=symbol,
        kind=InstrumentKind.US_OPTION,
        side=side,
        position_intent=intent,
        quantity=Decimal("1"),
        ratio_quantity=ratio,
    )


def make_position(**changes: object) -> ManagedOptionPosition:
    values: dict[str, Any] = {
        "position_id": "position-001",
        "account_id": "paper-account",
        "opening_intent_id": "intent-001",
        "opening_operation_hash": "operation-hash",
        "opening_receipt_hash": "receipt-hash",
        "horizon": Horizon.CATALYST,
        "direction": ThesisDirection.BULLISH,
        "long_leg": leg(LONG_SYMBOL, Side.BUY, PositionIntent.BUY_TO_OPEN),
        "short_leg": leg(SHORT_SYMBOL, Side.SELL, PositionIntent.SELL_TO_OPEN),
        "units": 1,
        "thesis_invalidation": "close if catalyst thesis is invalidated",
        "entry_basis_provenance": accounting(),
        "exit_policy_version": "exit-policy-v1",
    }
    values.update(changes)
    candidate = ManagedOptionPosition.model_construct(**values)
    values.setdefault("position_snapshot_hash", candidate._computed_position_snapshot_hash())
    return ManagedOptionPosition(**values)


def test_open_position_requires_non_empty_snapshot_binding() -> None:
    values = make_position().model_dump()
    values.pop("position_snapshot_hash")

    with pytest.raises(ValidationError, match="position_snapshot_hash|snapshot binding"):
        ManagedOptionPosition.model_validate(values)


def test_transition_requires_non_empty_snapshot_binding() -> None:
    with pytest.raises(ValidationError, match="position_snapshot_hash"):
        PositionTransition.create(
            transition_id="transition-001",
            position_id="position-001",
            from_state=PositionLifecycleState.OPEN,
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
            position_snapshot_hash="",
        )


def test_transition_integrity_hash_includes_snapshot_binding() -> None:
    common = {
        "transition_id": "transition-001",
        "position_id": "position-001",
        "from_state": PositionLifecycleState.OPEN,
        "to_state": PositionLifecycleState.CLOSE_PENDING,
        "reason": "exit signal",
        "observed_at": NOW,
        "evidence_hash": "evidence-001",
    }
    first = PositionTransition.create(**common, position_snapshot_hash="snapshot-a")
    second = PositionTransition.create(**common, position_snapshot_hash="snapshot-b")

    assert first.integrity_hash != second.integrity_hash


def test_position_accepts_valid_two_leg_option_open() -> None:
    position = make_position()
    assert position.state is PositionLifecycleState.OPEN
    assert position.expiration.isoformat() == "2028-01-20"
    assert position.thesis_invalidation == "close if catalyst thesis is invalidated"


def test_open_position_rejects_partial_entry_fill() -> None:
    provenance = accounting().model_copy(
        update={
            "broker_reported_filled_qty": Decimal("0.5"),
            "broker_reported_cost_basis": Decimal("50"),
        }
    )
    with pytest.raises(ValidationError, match="filled quantity"):
        make_position(entry_basis_provenance=provenance)


def test_position_requires_thesis_invalidation() -> None:
    values = make_position().model_dump()
    values.pop("thesis_invalidation")
    with pytest.raises(ValidationError, match="thesis_invalidation"):
        ManagedOptionPosition(**values)


@pytest.mark.parametrize(
    "change, message",
    [
        (
            {"long_leg": leg("SPY280121C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN)},
            "expiration",
        ),
        ({"short_leg": leg("SPY280120C00510000", Side.BUY, PositionIntent.SELL_TO_OPEN)}, "side"),
        ({"long_leg": leg("SPY280120C00500000", Side.BUY, PositionIntent.BUY_TO_CLOSE)}, "opening"),
        ({"long_leg": leg("SPY280120C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN, 2)}, "ratio"),
        ({"units": 0}, "units"),
        ({"units": 2}, "units"),
        (
            {"short_leg": leg(LONG_SYMBOL, Side.SELL, PositionIntent.SELL_TO_OPEN)},
            "distinct|strike",
        ),
        ({"thesis_invalidation": ""}, "invalidation"),
        ({"long_leg": leg("AAPL280120C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN)}, "symbols"),
        (
            {
                "long_leg": leg("SPY280120C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                "short_leg": leg("SPY280120C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
                "entry_basis_provenance": accounting(broker_cost="126"),
            },
            "authorized",
        ),
    ],
)
def test_position_rejects_invalid_open_invariants(change: dict[str, object], message: str) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        make_position(**change)


def test_transition_returns_new_version_and_is_immutable() -> None:
    position = make_position()
    pending = position.transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    assert position.state is PositionLifecycleState.OPEN
    assert pending.state is PositionLifecycleState.CLOSE_PENDING
    assert pending.version == 1
    assert pending.transitions[-1].previous_transition_hash == ""
    assert pending.transitions[-1].position_snapshot_hash == position.position_snapshot_hash
    assert pending.transitions[-1].transition_hash()


def test_transition_ledger_is_idempotent_and_rejects_identity_conflict() -> None:
    position = make_position()
    kwargs: dict[str, Any] = {
        "transition_id": "transition-001",
        "to_state": PositionLifecycleState.CLOSE_PENDING,
        "reason": "exit signal",
        "observed_at": NOW,
        "evidence_hash": "evidence-001",
    }
    first = position.transition(**kwargs)
    assert first.transition(**kwargs) == first
    with pytest.raises(ValueError, match="transition hash|transition identity"):
        first.transition(**{**kwargs, "reason": "different reason"})


def test_matching_replay_returns_canonical_validated_aggregate() -> None:
    position = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    forged = position.model_copy(update={"state": "close_pending"})
    replayed = forged.transition(
        transition_id="transition-001",
        to_state="close_pending",
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    assert isinstance(replayed.state, PositionLifecycleState)
    assert isinstance(replayed.long_leg, TradeLeg)
    assert replayed is not forged
    assert replayed == position


@pytest.mark.parametrize(
    "update, message",
    [
        ({"units": 2}, "leg quantity"),
        ({"long_leg": {"quantity": "2"}}, "long_leg"),
        ({"close_intent_id": ""}, "close reference"),
    ],
)
def test_matching_replay_rejects_forged_snapshot(update: dict[str, object], message: str) -> None:
    position = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    forged = position.model_copy(update=update)
    with pytest.raises((ValidationError, ValueError), match=message):
        forged.transition(
            transition_id="transition-001",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
        )


@pytest.mark.parametrize(
    "direction, long_symbol, short_symbol, message",
    [
        (ThesisDirection.BULLISH, "SPY280120P00500000", "SPY280120P00510000", "type"),
        (ThesisDirection.BULLISH, SHORT_SYMBOL, LONG_SYMBOL, "strike"),
        (ThesisDirection.BEARISH, SHORT_PUT_SYMBOL, LONG_PUT_SYMBOL, "strike"),
        (ThesisDirection.BEARISH, "SPY280120C00510000", "SPY280120C00500000", "type"),
        (ThesisDirection.NEUTRAL, LONG_SYMBOL, SHORT_SYMBOL, "direction"),
    ],
)
def test_vertical_requires_directional_strikes_and_directional_option_type(
    direction: ThesisDirection, long_symbol: str, short_symbol: str, message: str
) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        make_position(
            direction=direction,
            long_leg=leg(long_symbol, Side.BUY, PositionIntent.BUY_TO_OPEN),
            short_leg=leg(short_symbol, Side.SELL, PositionIntent.SELL_TO_OPEN),
        )


@pytest.mark.parametrize(
    "direction, long_symbol, short_symbol",
    [
        (ThesisDirection.BULLISH, LONG_SYMBOL, SHORT_SYMBOL),
        (ThesisDirection.BEARISH, LONG_PUT_SYMBOL, SHORT_PUT_SYMBOL),
    ],
)
def test_vertical_accepts_supported_direction_and_option_type(
    direction: ThesisDirection, long_symbol: str, short_symbol: str
) -> None:
    position = make_position(
        direction=direction,
        long_leg=leg(long_symbol, Side.BUY, PositionIntent.BUY_TO_OPEN),
        short_leg=leg(short_symbol, Side.SELL, PositionIntent.SELL_TO_OPEN),
    )

    assert position.direction is direction


@pytest.mark.parametrize("update, message", [("units", "leg quantity"), ("state", "state")])
def test_matching_replay_revalidates_forged_snapshot(update: str, message: str) -> None:
    position = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    forged = position.model_copy(update={update: 2 if update == "units" else "open"})
    with pytest.raises((ValidationError, ValueError), match=message):
        forged.transition(
            transition_id="transition-001",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
        )


def test_matching_replay_revalidates_forged_blank_close_reference() -> None:
    position = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    forged = position.model_copy(update={"close_intent_id": ""})
    with pytest.raises(ValueError, match="close reference"):
        forged.transition(
            transition_id="transition-001",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
        )


def test_matching_replay_rejects_structurally_valid_replacement_position() -> None:
    position = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    replacement = position.model_copy(
        update={
            "direction": ThesisDirection.BEARISH,
            "long_leg": leg(LONG_PUT_SYMBOL, Side.BUY, PositionIntent.BUY_TO_OPEN),
            "short_leg": leg(SHORT_PUT_SYMBOL, Side.SELL, PositionIntent.SELL_TO_OPEN),
        }
    )
    replacement = ManagedOptionPosition.model_construct(**replacement.model_dump())

    with pytest.raises(ValueError, match="snapshot|transition binding"):
        replacement.transition(
            transition_id="transition-001",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
        )


def test_position_transition_binding_is_required_and_tamper_evident() -> None:
    position = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    values = position.model_dump()
    values["transitions"] = (
        {
            key: value
            for key, value in values["transitions"][0].items()
            if key != "position_snapshot_hash"
        },
    )
    with pytest.raises(ValidationError, match="snapshot|binding|transition hash"):
        ManagedOptionPosition.model_validate(values)

    tampered = position.model_dump()
    tampered["transitions"] = (
        position.transitions[0].model_copy(update={"position_snapshot_hash": "tampered"}),
    )
    with pytest.raises((ValidationError, ValueError), match="snapshot|binding|transition hash"):
        ManagedOptionPosition.model_validate(tampered)


@pytest.mark.parametrize(
    "field, value",
    [
        ("close_intent_id", "other-close-intent"),
        ("close_operation_hash", "other-close-operation"),
        ("close_receipt_hash", "other-close-receipt"),
    ],
)
def test_transition_replay_compares_close_reference_payload(field: str, value: str) -> None:
    position = make_position()
    first = position.transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
        close_intent_id="close-intent",
        close_operation_hash="close-operation",
        close_receipt_hash="close-receipt",
    )
    with pytest.raises(ValueError, match="transition identity"):
        first.transition(
            transition_id="transition-001",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
            close_intent_id=value if field == "close_intent_id" else "close-intent",
            close_operation_hash=value if field == "close_operation_hash" else "close-operation",
            close_receipt_hash=value if field == "close_receipt_hash" else "close-receipt",
        )


def test_transition_replay_compares_previous_hash_payload() -> None:
    position = make_position()
    first = position.transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    tampered = first.model_copy(
        update={
            "transitions": (
                first.transitions[0].model_copy(update={"previous_transition_hash": "tampered"}),
            )
        }
    )
    with pytest.raises(ValueError, match="transition hash|transition identity"):
        tampered.transition(
            transition_id="transition-001",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
        )


def test_position_transition_hash_is_deterministic() -> None:
    transition = PositionTransition.create(
        transition_id="transition-001",
        position_id="position-001",
        from_state=PositionLifecycleState.OPEN,
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
        position_snapshot_hash="snapshot-001",
    )
    assert transition.transition_hash() == transition.model_copy().transition_hash()
    assert transition.integrity_hash


def test_transition_constructor_requires_hash_and_factory_computes_it() -> None:
    with pytest.raises(ValidationError, match="integrity_hash"):
        PositionTransition(
            transition_id="transition-001",
            position_id="position-001",
            from_state=PositionLifecycleState.OPEN,
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="exit signal",
            observed_at=NOW,
            evidence_hash="evidence-001",
            position_snapshot_hash="snapshot-001",
        )  # type: ignore[call-arg]

    transition = PositionTransition.create(
        transition_id="transition-001",
        position_id="position-001",
        from_state=PositionLifecycleState.OPEN,
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="exit signal",
        observed_at=NOW,
        evidence_hash="evidence-001",
        position_snapshot_hash="snapshot-001",
    )
    values = transition.model_dump()
    values.pop("integrity_hash")
    with pytest.raises(ValidationError, match="integrity_hash"):
        PositionTransition.model_validate(values)
    values["integrity_hash"] = transition.integrity_hash
    values["reason"] = "tampered"
    with pytest.raises(ValidationError, match="transition hash"):
        PositionTransition.model_validate(values)


def test_transition_factory_hashes_pydantic_normalized_payload() -> None:
    transition = PositionTransition.create(
        transition_id="transition-001",
        position_id="position-001",
        from_state="open",
        to_state="close_pending",
        reason="exit signal",
        observed_at="2026-09-03T12:00:00+00:00",
        evidence_hash="evidence-001",
        position_snapshot_hash="snapshot-001",
    )
    serialized = {
        "transition_id": "transition-001",
        "position_id": "position-001",
        "from_state": "open",
        "to_state": "close_pending",
        "reason": "exit signal",
        "observed_at": "2026-09-03T12:00:00+00:00",
        "evidence_hash": "evidence-001",
        "position_snapshot_hash": "snapshot-001",
        "integrity_hash": transition.integrity_hash,
    }

    assert PositionTransition.model_validate(serialized) == transition


def test_transition_accepts_string_state_and_round_trips_normalized_state() -> None:
    position = make_position().transition(
        transition_id="pending",
        to_state="close_pending",
        reason="close",
        observed_at="2026-09-03T12:00:00+00:00",
        evidence_hash="evidence",
    )
    restored = ManagedOptionPosition.model_validate(position.model_dump())
    assert restored.state is PositionLifecycleState.CLOSE_PENDING
    assert restored.transitions == position.transitions


def test_transition_rejects_blank_close_reference_before_candidate_creation() -> None:
    with pytest.raises(ValueError, match="close reference"):
        make_position().transition(
            transition_id="pending",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="close",
            observed_at=NOW,
            evidence_hash="evidence",
            close_intent_id=" ",
        )


def test_transition_result_is_revalidated_after_state_update() -> None:
    position = make_position()
    pending = position.transition(
        transition_id="pending",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="close",
        observed_at=NOW,
        evidence_hash="evidence",
    )
    tampered = pending.model_copy(update={"units": 2})
    with pytest.raises(ValidationError, match="leg quantity"):
        ManagedOptionPosition.model_validate(tampered.model_dump())


def test_terminal_ledger_rejects_new_self_event_when_deserialized() -> None:
    position = make_position().transition(
        transition_id="failed",
        to_state=PositionLifecycleState.RECONCILIATION_FAILED,
        reason="failed",
        observed_at=NOW,
        evidence_hash="failed-evidence",
    )
    extra = PositionTransition.create(
        transition_id="new-self-event",
        position_id=position.position_id,
        from_state=position.state,
        to_state=position.state,
        reason="new self event",
        observed_at=NOW,
        evidence_hash="new-evidence",
        position_snapshot_hash=position.position_snapshot_hash,
    )
    values = position.model_dump()
    values["transitions"] = (*values["transitions"], extra)
    values["version"] = 2
    with pytest.raises(ValidationError, match="terminal/review"):
        ManagedOptionPosition.model_validate(values)


def test_close_refs_must_match_top_level_and_ledger_on_deserialization() -> None:
    position = make_position().transition(
        transition_id="pending",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="close",
        observed_at=NOW,
        evidence_hash="evidence",
        close_intent_id="close-intent-a",
    )
    top_level_tampered = position.model_dump()
    top_level_tampered["close_intent_id"] = "close-intent-b"
    with pytest.raises(ValidationError, match="close reference"):
        ManagedOptionPosition.model_validate(top_level_tampered)

    ledger_tampered = position.model_dump()
    ledger_tampered["transitions"] = (
        position.transitions[0].model_copy(update={"close_intent_id": "close-intent-b"}),
    )
    with pytest.raises(ValidationError, match="transition hash|close reference"):
        ManagedOptionPosition.model_validate(ledger_tampered)


@pytest.mark.parametrize(
    "change, message",
    [
        (
            {"long_leg": leg(LONG_SYMBOL, Side.BUY, PositionIntent.BUY_TO_OPEN, 2)},
            "ratio",
        ),
        (
            {"long_leg": leg(LONG_SYMBOL, Side.BUY, PositionIntent.BUY_TO_OPEN), "units": 2},
            "quantity",
        ),
        ({"entry_basis_provenance": accounting(), "units": 2}, "units"),
        ({"entry_basis_provenance": accounting(broker_cost="101")}, "cost basis"),
    ],
)
def test_position_rejects_forged_exposure_or_accounting(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        make_position(**change)


@pytest.mark.parametrize(
    "broker_cost, fill_price, fill_qty, message",
    [
        ("100", "1.01", "1", "cost basis"),
        ("100", "1", "1.1", "cost basis"),
    ],
)
def test_position_rejects_inconsistent_accounting_formula(
    broker_cost: str, fill_price: str, fill_qty: str, message: str
) -> None:
    provenance = accounting(broker_cost=broker_cost).model_copy(
        update={
            "broker_reported_fill_price": Decimal(fill_price),
            "broker_reported_filled_qty": Decimal(fill_qty),
        }
    )
    with pytest.raises((ValidationError, ValueError), match=message):
        make_position(entry_basis_provenance=provenance)


def test_position_rejects_tampered_transition_ledger() -> None:
    first = make_position().transition(
        transition_id="transition-001",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="pending",
        observed_at=NOW,
        evidence_hash="evidence-001",
    )
    second = first.transition(
        transition_id="transition-002",
        to_state=PositionLifecycleState.CLOSE_FILLED,
        reason="filled",
        observed_at=NOW,
        evidence_hash="evidence-002",
    )
    tampered_cases = (
        second.model_copy(
            update={
                "transitions": (
                    first.transitions[0].model_copy(update={"previous_transition_hash": "bad"}),
                    second.transitions[1],
                )
            }
        ),
        second.model_copy(
            update={
                "transitions": (
                    second.transitions[0],
                    second.transitions[1].model_copy(
                        update={"from_state": PositionLifecycleState.OPEN}
                    ),
                )
            }
        ),
        second.model_copy(update={"transitions": second.transitions + (second.transitions[1],)}),
        second.model_copy(update={"state": PositionLifecycleState.OPEN}),
        second.model_copy(update={"version": 1}),
    )
    for tampered in tampered_cases:
        with pytest.raises((ValidationError, ValueError), match="transition|version|state"):
            ManagedOptionPosition.model_validate(tampered.model_dump())


def test_managed_position_is_frozen() -> None:
    position = make_position()
    with pytest.raises(Exception, match="frozen"):
        position.state = PositionLifecycleState.CLOSE_PENDING


@pytest.mark.parametrize("field", ["close_intent_id", "close_operation_hash", "close_receipt_hash"])
def test_close_references_must_be_non_empty(field: str) -> None:
    with pytest.raises((ValidationError, ValueError), match="close"):
        make_position(**{field: ""})


@pytest.mark.parametrize(
    "from_state, to_state",
    [
        (PositionLifecycleState.OPEN, PositionLifecycleState.CLOSE_FILLED),
        (PositionLifecycleState.CLOSE_PENDING, PositionLifecycleState.OPEN),
        (PositionLifecycleState.CLOSE_FILLED, PositionLifecycleState.OPEN),
        (PositionLifecycleState.CLOSED_RECONCILED, PositionLifecycleState.OPEN),
    ],
)
def test_transition_graph_rejects_invalid_moves(
    from_state: PositionLifecycleState, to_state: PositionLifecycleState
) -> None:
    position = make_position()
    if from_state is PositionLifecycleState.CLOSE_PENDING:
        position = position.transition(
            transition_id="pending",
            to_state=from_state,
            reason="pending",
            observed_at=NOW,
            evidence_hash="pending-evidence",
        )
    elif from_state is PositionLifecycleState.CLOSE_FILLED:
        position = position.transition(
            transition_id="pending",
            to_state=PositionLifecycleState.CLOSE_PENDING,
            reason="pending",
            observed_at=NOW,
            evidence_hash="pending-evidence",
        ).transition(
            transition_id="filled",
            to_state=from_state,
            reason="filled",
            observed_at=NOW,
            evidence_hash="filled-evidence",
        )
    elif from_state is PositionLifecycleState.CLOSED_RECONCILED:
        position = (
            position.transition(
                transition_id="pending",
                to_state=PositionLifecycleState.CLOSE_PENDING,
                reason="pending",
                observed_at=NOW,
                evidence_hash="pending-evidence",
            )
            .transition(
                transition_id="filled",
                to_state=PositionLifecycleState.CLOSE_FILLED,
                reason="filled",
                observed_at=NOW,
                evidence_hash="filled-evidence",
            )
            .transition(
                transition_id="closed",
                to_state=from_state,
                reason="closed",
                observed_at=NOW,
                evidence_hash="closed-evidence",
            )
        )
    with pytest.raises(ValueError, match="transition"):
        position.transition(
            transition_id="transition-invalid",
            to_state=to_state,
            reason="invalid",
            observed_at=NOW,
            evidence_hash="evidence",
        )


def test_review_and_terminal_states_only_accept_idempotent_self_observation() -> None:
    for state in (
        PositionLifecycleState.CLOSED_RECONCILED,
        PositionLifecycleState.EXPIRATION_REVIEW,
        PositionLifecycleState.ASSIGNMENT_REVIEW,
        PositionLifecycleState.RECONCILIATION_FAILED,
    ):
        position = make_position()
        if state is PositionLifecycleState.CLOSED_RECONCILED:
            position = position.transition(
                transition_id="pending",
                to_state=PositionLifecycleState.CLOSE_PENDING,
                reason="pending",
                observed_at=NOW,
                evidence_hash="pending-evidence",
            ).transition(
                transition_id="filled",
                to_state=PositionLifecycleState.CLOSE_FILLED,
                reason="filled",
                observed_at=NOW,
                evidence_hash="filled-evidence",
            )
        position = position.transition(
            transition_id=f"{state.value}-observation",
            to_state=state,
            reason="observe",
            observed_at=NOW,
            evidence_hash=f"{state.value}-evidence",
        )
        observed = position.transition(
            transition_id=f"{state.value}-observation",
            to_state=state,
            reason="observe",
            observed_at=NOW,
            evidence_hash=f"{state.value}-evidence",
        )
        assert observed == position
        with pytest.raises(ValueError, match="terminal/review"):
            position.transition(
                transition_id="new-observation-id",
                to_state=state,
                reason="observe",
                observed_at=NOW,
                evidence_hash="new-evidence",
            )


def test_position_rejects_illegal_transition_during_deserialization() -> None:
    position = make_position()
    illegal = PositionTransition.create(
        transition_id="illegal",
        position_id="position-001",
        from_state=PositionLifecycleState.OPEN,
        to_state=PositionLifecycleState.CLOSE_FILLED,
        reason="skipped pending",
        observed_at=NOW,
        evidence_hash="evidence",
        position_snapshot_hash=position.position_snapshot_hash,
    )
    values = position.model_dump()
    values.update(
        {"state": PositionLifecycleState.CLOSE_FILLED, "version": 1, "transitions": (illegal,)}
    )
    with pytest.raises(ValidationError, match="transition is not allowed"):
        ManagedOptionPosition(**values)


def test_close_references_are_write_once() -> None:
    position = make_position().transition(
        transition_id="pending",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="close",
        observed_at=NOW,
        evidence_hash="evidence",
        close_intent_id="close-intent",
        close_operation_hash="close-operation",
        close_receipt_hash="close-receipt",
    )
    retained = position.transition(
        transition_id="filled",
        to_state=PositionLifecycleState.CLOSE_FILLED,
        reason="filled",
        observed_at=NOW,
        evidence_hash="filled-evidence",
    )
    assert retained.close_intent_id == "close-intent"
    with pytest.raises(ValueError, match="close reference"):
        position.transition(
            transition_id="filled-different-ref",
            to_state=PositionLifecycleState.CLOSE_FILLED,
            reason="filled",
            observed_at=NOW,
            evidence_hash="filled-evidence",
            close_intent_id="other-close-intent",
        )


def test_last_transition_payload_tampering_is_rejected_on_deserialization() -> None:
    position = make_position().transition(
        transition_id="pending",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="close",
        observed_at=NOW,
        evidence_hash="evidence",
    )
    tampered = position.transitions[0].model_copy(update={"reason": "tampered"})
    values = position.model_dump()
    values["transitions"] = (tampered,)
    with pytest.raises(ValidationError, match="transition hash"):
        ManagedOptionPosition(**values)


def test_transition_preserves_position_shape_and_accepts_close_references() -> None:
    pending = make_position().transition(
        transition_id="pending",
        to_state=PositionLifecycleState.CLOSE_PENDING,
        reason="close",
        observed_at=NOW,
        evidence_hash="evidence",
        close_intent_id="close-intent",
        close_operation_hash="close-operation",
        close_receipt_hash="close-receipt",
    )
    assert pending.long_leg == make_position().long_leg
    assert pending.short_leg == make_position().short_leg
    assert pending.units == 1
    assert pending.close_intent_id == "close-intent"
