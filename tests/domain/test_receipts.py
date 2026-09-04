from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.domain.closed_accounting import close_position_accounting
from convictionos.domain.mandates import PolicyOutcome
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.receipts import DecisionReceipt
from tests.domain.test_closed_accounting import capability, opening
from tests.domain.test_trade_intent import make_intent


def test_receipt_hash_links_to_previous_receipt() -> None:
    intent = make_intent()
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

    assert receipt.receipt_hash() == receipt.model_copy().receipt_hash()
    assert receipt.model_copy(update={"broker_status": "rejected"}).receipt_hash() != (
        receipt.receipt_hash()
    )


def test_enriched_facts_change_receipt_hash_but_legacy_defaults_do_not() -> None:
    intent = make_intent()
    base = dict(
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
    )
    legacy = DecisionReceipt(**base)
    capability = build_market_data_capability(
        UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, datetime(2026, 8, 30, tzinfo=UTC)
    )
    accounting = PaperExecutionAccounting.try_from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("1.25"),
        spread_units=1,
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("1"),
    )
    enriched = legacy.model_copy(update={"market_data": capability, "paper_accounting": accounting})

    legacy_payload = legacy.model_dump(mode="json", exclude_none=True)
    assert "market_data" not in legacy_payload
    assert "paper_accounting" not in legacy_payload
    assert DecisionReceipt.model_validate(legacy_payload) == legacy
    assert legacy.receipt_hash() == DecisionReceipt(**base).receipt_hash()
    assert enriched.receipt_hash() != legacy.receipt_hash()


def test_receipt_rejects_mismatched_accounting_capability() -> None:
    intent = make_intent()
    first = build_market_data_capability(
        UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, datetime(2026, 8, 30, tzinfo=UTC)
    )
    second = build_market_data_capability(
        UnderlyingFeed.SIP, OptionsFeed.OPRA, datetime(2026, 8, 30, tzinfo=UTC)
    )
    accounting = PaperExecutionAccounting.try_from_open_fill(
        capability=first,
        authorized_limit_price=Decimal("1.25"),
        spread_units=1,
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("1"),
    )
    with pytest.raises(ValueError, match="capability"):
        DecisionReceipt(
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
            market_data=second,
            paper_accounting=accounting,
        )


def test_receipt_rejects_unknown_payload_fields() -> None:
    with pytest.raises(ValidationError):
        DecisionReceipt.model_validate(
            {
                "receipt_id": "receipt-001",
                "intent_id": "intent-001",
                "operation_hash": "operation-hash",
                "evidence_snapshot_hash": "evidence-hash",
                "mandate_id": "mandate-001",
                "mandate_version": 1,
                "policy_outcome": "allow",
                "policy_reasons": [],
                "broker_order_id": "order-1",
                "broker_status": "filled",
                "previous_receipt_hash": "genesis",
                "unexpected": "tampered",
            }
        )


def test_closed_accounting_requires_position_and_exit_references() -> None:
    accounting = close_position_accounting(
        opening=opening(),
        close_fill_credit=Decimal("1.80"),
        close_filled_units=Decimal("2"),
        authorized_close_credit_floor=Decimal("1.50"),
        capability=capability(),
    )
    with pytest.raises(ValueError, match="position reference"):
        DecisionReceipt(
            receipt_id="receipt-close",
            intent_id="position-001-close-v1",
            operation_hash="operation-hash",
            evidence_snapshot_hash="evidence-hash",
            mandate_id="intent-001",
            mandate_version=1,
            policy_outcome=PolicyOutcome.ALLOW,
            policy_reasons=(),
            broker_order_id="order-close",
            broker_status="filled",
            previous_receipt_hash="genesis",
            market_data=capability(),
            closed_accounting=accounting,
        )
