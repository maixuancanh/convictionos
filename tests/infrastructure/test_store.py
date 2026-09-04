import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.domain.mandates import PolicyOutcome
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.trading import IntentState
from convictionos.infrastructure.models import OutboxRow, RiskReservationRow
from convictionos.infrastructure.store import CapacityExceeded, ReservationConflict, Store
from tests.domain.test_trade_intent import make_intent


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_existing_intent(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    first = await store.create_intent(make_intent())
    second = await store.create_intent(make_intent())
    assert first == second == "intent-001"


@pytest.mark.asyncio
async def test_persists_and_verifies_receipt_hash(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    receipt = DecisionReceipt(
        receipt_id="receipt-001",
        intent_id=intent.intent_id,
        operation_hash=intent.operation_hash(),
        evidence_snapshot_hash=intent.data_snapshot_hash,
        mandate_id=intent.mandate_id,
        mandate_version=intent.mandate_version,
        policy_outcome=PolicyOutcome.ALLOW,
        policy_reasons=(),
        broker_order_id="fake-intent-001",
        broker_status="filled",
        previous_receipt_hash="genesis",
    )

    await store.save_receipt(receipt)

    assert await store.get_receipt(intent.intent_id) == receipt


@pytest.mark.asyncio
async def test_round_trips_enriched_receipt(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    intent = make_intent()
    await store.create_intent(intent)
    capability = build_market_data_capability(
        UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, intent.created_at
    )
    accounting = PaperExecutionAccounting.try_from_open_fill(
        capability=capability,
        authorized_limit_price=intent.limit_price,
        spread_units=1,
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("1"),
    )
    receipt = DecisionReceipt(
        receipt_id="receipt-001",
        intent_id=intent.intent_id,
        operation_hash=intent.operation_hash(),
        evidence_snapshot_hash=intent.data_snapshot_hash,
        mandate_id=intent.mandate_id,
        mandate_version=intent.mandate_version,
        policy_outcome=PolicyOutcome.ALLOW,
        policy_reasons=(),
        broker_order_id="order-1",
        broker_status="filled",
        previous_receipt_hash="genesis",
        market_data=capability,
        paper_accounting=accounting,
    )
    await store.save_receipt(receipt)
    assert await store.get_receipt(intent.intent_id) == receipt


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_with_different_hash_is_rejected(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())

    mutated = make_intent(Decimal("2"))

    with pytest.raises(ValueError, match="idempotency key already exists"):
        await store.create_intent(mutated)


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_treated_as_idempotency_dedupe(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())
    conflicting = make_intent().model_copy(update={"idempotency_key": "intent-002-v1"})

    with pytest.raises(IntegrityError):
        await store.create_intent(conflicting)


@pytest.mark.asyncio
async def test_reservations_are_atomic_per_account(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())
    second_intent = make_intent().model_copy(
        update={"intent_id": "intent-002", "idempotency_key": "intent-002-v1"}
    )
    await store.create_intent(second_intent)

    results = await asyncio.gather(
        store.authorize_with_reservation(
            "intent-001", "paper-account", Decimal("125"), Decimal("200")
        ),
        store.authorize_with_reservation(
            "intent-002", "paper-account", Decimal("125"), Decimal("200")
        ),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, CapacityExceeded) for result in results) == 1
    states = {await store.get_state("intent-001"), await store.get_state("intent-002")}
    assert states == {IntentState.VALIDATED, IntentState.AUTHORIZED}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount", "capacity"),
    [
        (Decimal("0"), Decimal("200")),
        (Decimal("-1"), Decimal("200")),
        (Decimal("125"), Decimal("0")),
        (Decimal("125"), Decimal("-1")),
    ],
)
async def test_authorize_rejects_non_positive_risk_inputs(
    engine: AsyncEngine,
    amount: Decimal,
    capacity: Decimal,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())

    with pytest.raises(ValueError, match="positive"):
        await store.authorize_with_reservation("intent-001", "paper-account", amount, capacity)


@pytest.mark.asyncio
async def test_repeated_authorization_returns_success_without_duplicate_rows(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())

    await store.authorize_with_reservation(
        "intent-001", "paper-account", Decimal("125"), Decimal("200")
    )
    await store.authorize_with_reservation(
        "intent-001", "paper-account", Decimal("125"), Decimal("200")
    )

    async with sessions() as session:
        reservation_count = await session.scalar(
            select(func.count())
            .select_from(RiskReservationRow)
            .where(RiskReservationRow.intent_id == "intent-001")
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxRow)
            .where(OutboxRow.aggregate_id == "intent-001")
        )

    assert reservation_count == 1
    assert outbox_count == 1
    assert await store.get_state("intent-001") is IntentState.AUTHORIZED


@pytest.mark.asyncio
async def test_repeated_authorization_conflicts_when_existing_reservation_changes(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())
    await store.authorize_with_reservation(
        "intent-001", "paper-account", Decimal("125"), Decimal("200")
    )

    async with sessions() as session:
        await session.execute(
            text("UPDATE risk_reservations SET amount = :amount WHERE intent_id = :intent_id"),
            {"amount": Decimal("100"), "intent_id": "intent-001"},
        )
        await session.commit()

    with pytest.raises(ReservationConflict, match="already authorized"):
        await store.authorize_with_reservation(
            "intent-001", "paper-account", Decimal("125"), Decimal("200")
        )


@pytest.mark.asyncio
async def test_authorize_rejects_account_mismatch(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    await store.create_intent(make_intent())

    with pytest.raises(ReservationConflict, match="account does not match"):
        await store.authorize_with_reservation(
            "intent-001", "other-account", Decimal("125"), Decimal("200")
        )
