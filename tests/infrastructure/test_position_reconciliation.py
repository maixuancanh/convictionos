from decimal import Decimal

import pytest
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
from convictionos.infrastructure.alpaca_market import PaperPosition
from convictionos.infrastructure.store import Store
from tests.domain.test_positions import make_position
from tests.domain.test_trade_intent import make_intent


@pytest.mark.asyncio
async def test_exact_broker_spread_has_no_reconciliation_finding(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    await store.create_intent(make_intent())
    position = make_position()
    await store.create_or_load_position(position)

    findings = await store.find_unmanaged_reconciled_openings(
        "paper-account",
        (
            PaperPosition(
                symbol=position.long_leg.symbol,
                quantity=Decimal("1"),
                market_value=Decimal("100"),
                unrealized_pl=Decimal("0"),
            ),
            PaperPosition(
                symbol=position.short_leg.symbol,
                quantity=Decimal("1"),
                market_value=Decimal("-50"),
                unrealized_pl=Decimal("0"),
            ),
        ),
    )

    assert findings == ()


@pytest.mark.asyncio
async def test_partial_or_unknown_broker_spread_is_a_finding(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    await store.create_intent(make_intent())
    position = make_position()
    await store.create_or_load_position(position)

    findings = await store.find_unmanaged_reconciled_openings(
        "paper-account",
        (
            PaperPosition(
                symbol=position.long_leg.symbol,
                quantity=Decimal("2"),
                market_value=Decimal("200"),
                unrealized_pl=Decimal("0"),
            ),
            PaperPosition(
                symbol="QQQ280120C00500000",
                quantity=Decimal("1"),
                market_value=Decimal("100"),
                unrealized_pl=Decimal("0"),
            ),
        ),
    )

    assert {item.symbol for item in findings} == {
        position.long_leg.symbol,
        "QQQ280120C00500000",
    }


@pytest.mark.asyncio
async def test_reconciled_opening_promotes_once_when_receipt_and_legs_match(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, intent.max_loss, Decimal("1000")
    )
    await store.claim_submission(intent.intent_id)
    await store.record_broker_order(intent.intent_id, "order-1", IntentState.RECONCILED)
    capability = build_market_data_capability(
        UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, intent.created_at
    )
    accounting = PaperExecutionAccounting.from_open_fill(
        capability=capability,
        authorized_limit_price=intent.limit_price,
        spread_units=1,
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("1"),
    )
    await store.save_receipt(
        DecisionReceipt(
            receipt_id="receipt-open",
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
    )
    broker_positions = tuple(
        PaperPosition(
            symbol=leg.symbol,
            quantity=Decimal("1"),
            market_value=Decimal("1"),
            unrealized_pl=Decimal("0"),
        )
        for leg in intent.legs
    )

    first, findings = await store.promote_reconciled_openings(
        "paper-account", broker_positions
    )
    second, second_findings = await store.promote_reconciled_openings(
        "paper-account", broker_positions
    )

    assert len(first) == 1
    assert first[0].state.value == "open"
    assert first[0].opening_thesis_ref == intent.thesis_ref
    assert findings == ()
    assert second == ()
    assert second_findings == ()
