from datetime import UTC, datetime
from decimal import Decimal

import pytest

from convictionos.application.exit_policy import ExitPolicyEngine
from convictionos.application.position_lifecycle import (
    PositionLifecycleService,
    map_broker_activity_state,
    map_close_order_state,
)
from convictionos.domain.positions import PositionLifecycleState
from convictionos.infrastructure.alpaca_activities import (
    BrokerActivityType,
    SanitizedBrokerActivity,
)
from convictionos.infrastructure.alpaca_market import PaperPosition
from convictionos.infrastructure.brokers import BrokerOrderStatus
from tests.application.test_exit_policy import observation, signal
from tests.domain.test_positions import make_position

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class StoreStub:
    def __init__(self, positions, findings=()):
        self.positions = positions
        self.findings = findings
        self.attached = []
        self.cursors = {}

    async def list_managed_positions(self, states=None):
        return self.positions

    async def find_unmanaged_reconciled_openings(self, account_id, broker_positions):
        return self.findings

    async def attach_close_intent(self, position_id, intent, transition, expected_version):
        self.attached.append((position_id, intent, transition, expected_version))
        return self.positions[0]

    async def transition_position(self, position, transition, expected_version):
        self.attached.append((position.position_id, None, transition, expected_version))
        return position

    async def get_activity_cursor(self, account_id, activity_type):
        return self.cursors.get((account_id, activity_type))

    async def advance_activity_cursor(self, account_id, activity_type, cursor):
        self.cursors[(account_id, activity_type)] = cursor


class BrokerStub:
    def __init__(self, positions=()):
        self.positions = positions

    async def get_positions(self):
        return self.positions


class ExitStub:
    async def evaluate(self, position, *, now):
        context = observation(position=position, now=now, observed_at=now)
        context = context.model_copy(
            update={
                "signals": signal(
                    snapshot_hash=context.close_quotes.snapshot_hash,
                    observed_at=now,
                )
            }
        )
        return ExitPolicyEngine().evaluate(context)


class ExecutionStub:
    def __init__(self):
        self.intent_ids = []

    async def execute(self, intent_id):
        self.intent_ids.append(intent_id)


class ActivityStub:
    def __init__(self, activity):
        self.activity = activity
        self.calls = []

    async def list_activities(self, activity_type, *, after=None, until=None, page_token=None):
        self.calls.append((activity_type, page_token))
        if activity_type is BrokerActivityType.OPASN and page_token is None:
            return (self.activity,), None
        return (), None


class OrderLookupStub:
    def __init__(self, order=None, error=None):
        self.order = order
        self.error = error

    async def get_by_client_order_id(self, client_order_id):
        if self.error is not None:
            raise self.error
        return self.order


def broker_activity(activity_type, symbol):
    values = {
        "activity_id": "activity-001",
        "activity_type": activity_type,
        "event_time": NOW,
        "symbol": symbol,
        "quantity": Decimal("1"),
    }
    provisional = SanitizedBrokerActivity.model_construct(content_hash="provisional", **values)
    return SanitizedBrokerActivity(content_hash=provisional.computed_hash(), **values)


@pytest.mark.asyncio
async def test_lifecycle_result_blocks_entry_on_reconciliation_finding() -> None:
    unexpected = PaperPosition(
        symbol="QQQ280120C00500000",
        quantity=Decimal("1"),
        market_value=Decimal("1"),
        unrealized_pl=Decimal("0"),
    )
    result = await PositionLifecycleService(
        StoreStub([make_position()], (unexpected,)), BrokerStub(), "paper-account"
    ).run_cycle(now=NOW, allow_close_submission=True)

    assert result.block_new_entries
    assert result.review_reasons == ("broker_position_mismatch",)
    assert result.close_submissions == 0
    assert "broker_order_id" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_lifecycle_attaches_and_executes_one_close_intent() -> None:
    store = StoreStub([make_position()])
    position = store.positions[0]
    execution = ExecutionStub()
    result = await PositionLifecycleService(
        store,
        BrokerStub(
            (
                PaperPosition(
                    symbol=position.long_leg.symbol,
                    quantity=Decimal("1"),
                    market_value=Decimal("1"),
                    unrealized_pl=Decimal("0"),
                ),
                PaperPosition(
                    symbol=position.short_leg.symbol,
                    quantity=Decimal("1"),
                    market_value=Decimal("-1"),
                    unrealized_pl=Decimal("0"),
                ),
            )
        ),
        "paper-account",
        exit_decisions=ExitStub(),
        close_execution=execution,
    ).run_cycle(now=NOW, allow_close_submission=True)

    assert result.transitioned_public_ids == ("position-001",)
    assert result.close_submissions == 1
    assert execution.intent_ids == ["position-001-close-v1"]
    assert len(store.attached) == 1
    assert store.attached[0][2].to_state.value == "close_pending"


@pytest.mark.asyncio
async def test_close_filled_reconciles_only_when_both_legs_are_absent() -> None:
    opened = make_position()
    pending = opened.transition(
        transition_id="pending",
        to_state="close_pending",
        reason="take profit",
        observed_at=NOW,
        evidence_hash="decision-hash",
        close_intent_id="position-001-close-v1",
        close_operation_hash="operation-hash-close",
    )
    filled = pending.transition(
        transition_id="filled",
        to_state="close_filled",
        reason="broker filled exact close",
        observed_at=NOW,
        evidence_hash="broker-fill-hash",
    )
    store = StoreStub([filled])
    result = await PositionLifecycleService(
        store, BrokerStub(), "paper-account"
    ).run_cycle(now=NOW, allow_close_submission=False)

    assert result.transitioned_public_ids == ("position-001",)
    assert store.attached[0][2].to_state.value == "closed_reconciled"


@pytest.mark.asyncio
async def test_assignment_activity_moves_matching_position_to_review_and_advances_cursor() -> None:
    position = make_position()
    store = StoreStub([position])
    activity = ActivityStub(broker_activity(BrokerActivityType.OPASN, position.long_leg.symbol))
    result = await PositionLifecycleService(
        store, BrokerStub(), "paper-account", activities=activity
    ).run_cycle(now=NOW, allow_close_submission=False)

    assert result.block_new_entries
    assert result.review_reasons == ()
    assert result.transitioned_public_ids == ("position-001",)
    assert store.cursors[("paper-account", "OPASN")] == "activity-001"
    assert store.attached[0][2].to_state.value == "assignment_review"


@pytest.mark.asyncio
async def test_close_order_fill_is_reconciled_from_broker_lookup() -> None:
    opened = make_position()
    pending = opened.transition(
        transition_id="pending",
        to_state="close_pending",
        reason="take profit",
        observed_at=NOW,
        evidence_hash="decision-hash",
        close_intent_id="position-001-close-v1",
        close_operation_hash="operation-hash-close",
    )
    from convictionos.infrastructure.brokers import BrokerOrder

    order = BrokerOrder(
        order_id="broker-close-001",
        client_order_id="position-001-close-v1",
        status=BrokerOrderStatus.FILLED,
        filled_avg_price=Decimal("0.40"),
        filled_qty=Decimal("1"),
        filled_at=NOW,
    )
    store = StoreStub([pending])
    result = await PositionLifecycleService(
        store, BrokerStub(), "paper-account", orders=OrderLookupStub(order)
    ).run_cycle(now=NOW, allow_close_submission=False)

    assert result.transitioned_public_ids == ("position-001",)
    assert store.attached[0][2].to_state is PositionLifecycleState.CLOSE_FILLED


@pytest.mark.parametrize(
    ("status", "present", "expected"),
    [
        (BrokerOrderStatus.NEW, True, "close_pending"),
        (BrokerOrderStatus.PARTIALLY_FILLED, True, "close_pending"),
        (BrokerOrderStatus.FILLED, False, "close_filled"),
        (BrokerOrderStatus.REJECTED, True, "open"),
        (BrokerOrderStatus.CANCELED, False, "reconciliation_failed"),
    ],
)
def test_close_order_state_mapping_is_fail_closed(status, present, expected) -> None:
    assert map_close_order_state(status, complete_spread_present=present).value == expected


@pytest.mark.parametrize(
    ("activity_type", "expected"),
    [
        (BrokerActivityType.OPASN, "assignment_review"),
        (BrokerActivityType.OPEXP, "expiration_review"),
        (BrokerActivityType.OPXRC, "expiration_review"),
        (BrokerActivityType.OPTRD, None),
        (BrokerActivityType.FEE, None),
    ],
)
def test_activity_state_mapping_never_invents_close(activity_type, expected) -> None:
    state = map_broker_activity_state(activity_type)
    assert (state.value if state is not None else None) == expected
