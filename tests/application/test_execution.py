from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.application.execution import ExecutionOrchestrator
from convictionos.domain.trading import IntentState
from convictionos.infrastructure.brokers import (
    BrokerLookupUnavailable,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerRejected,
    BrokerReplacementPending,
    FakeBroker,
    UnknownSubmission,
)
from convictionos.infrastructure.store import (
    CapacityExceeded,
    Store,
    SubmissionClaim,
    validate_broker_transition,
)
from tests.domain.test_trade_intent import make_intent


class MemoryStore:
    def __init__(self, intent, state: IntentState = IntentState.AUTHORIZED) -> None:
        self.intent = intent
        self.state = state
        self.reset_reasons: list[str] = []
        self.recorded: list[tuple[str, IntentState]] = []
        self.terminal_calls: list[tuple[IntentState, str]] = []

    async def load_intent(self, intent_id: str):
        assert intent_id == self.intent.intent_id
        return self.intent

    async def claim_submission(self, intent_id: str) -> SubmissionClaim:
        assert intent_id == self.intent.intent_id
        if self.state is IntentState.AUTHORIZED:
            self.state = IntentState.SUBMITTING
            return SubmissionClaim(True, None, IntentState.SUBMITTING)
        return SubmissionClaim(False, "fake-order", self.state)

    async def recover_submission(self, intent_id: str, reason: str) -> None:
        assert intent_id == self.intent.intent_id
        if self.state is not IntentState.SUBMITTING:
            raise ValueError(f"cannot reset submission in state {self.state.value}")
        self.reset_reasons.append(reason)
        self.state = IntentState.AUTHORIZED

    async def mark_ack_unknown(self, intent_id: str, reason: str) -> None:
        assert intent_id == self.intent.intent_id
        self.reset_reasons.append(reason)
        self.state = IntentState.ACK_UNKNOWN

    async def record_broker_order(
        self, intent_id: str, broker_order_id: str, state: IntentState
    ) -> None:
        self.recorded.append((broker_order_id, state))
        self.state = state

    async def mark_not_filled(self, intent_id: str, state: IntentState, reason: str) -> None:
        self.terminal_calls.append((state, reason))
        self.state = state


@pytest.mark.asyncio
async def test_unknown_submit_is_recovered_without_duplicate_order(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    broker = FakeBroker(raise_after_accept=True)
    orchestrator = ExecutionOrchestrator(store, broker)

    order = await orchestrator.execute(intent.intent_id)
    second = await orchestrator.execute(intent.intent_id)

    assert order.order_id == second.order_id
    assert broker.submit_calls == 1
    assert await store.get_state(intent.intent_id) is IntentState.RECONCILED


@pytest.mark.asyncio
async def test_known_broker_rejection_releases_reservation(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    orchestrator = ExecutionOrchestrator(store, FakeBroker(reject=True))

    with pytest.raises(BrokerRejected):
        await orchestrator.execute(intent.intent_id)

    assert await store.get_state(intent.intent_id) is IntentState.REJECTED
    replacement = make_intent().model_copy(
        update={"intent_id": "intent-002", "idempotency_key": "intent-002-v1"}
    )
    await store.create_intent(replacement)
    await store.authorize_with_reservation(
        replacement.intent_id, replacement.account_id, Decimal("125"), Decimal("200")
    )
    assert await store.get_state(replacement.intent_id) is IntentState.AUTHORIZED


@pytest.mark.asyncio
async def test_replacement_pending_preserves_risk_and_never_resubmits(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )

    class ReplacedBroker:
        submit_calls = 0

        async def submit(self, submitted_intent):
            self.submit_calls += 1
            raise BrokerReplacementPending(submitted_intent.idempotency_key)

        async def get_by_client_order_id(self, client_order_id: str):
            raise BrokerReplacementPending(client_order_id)

    broker = ReplacedBroker()
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(BrokerReplacementPending):
        await orchestrator.execute(intent.intent_id)
    assert await store.get_state(intent.intent_id) is IntentState.REPLACEMENT_PENDING

    with pytest.raises(BrokerReplacementPending):
        await orchestrator.execute(intent.intent_id)
    assert broker.submit_calls == 1

    replacement = make_intent().model_copy(
        update={"intent_id": "intent-002", "idempotency_key": "intent-002-v1"}
    )
    await store.create_intent(replacement)
    with pytest.raises(CapacityExceeded):
        await store.authorize_with_reservation(
            replacement.intent_id, replacement.account_id, Decimal("125"), Decimal("200")
        )


@pytest.mark.asyncio
async def test_lookup_outage_leaves_submission_held_without_resubmitting(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )

    class LookupOutageBroker:
        submit_calls = 0

        async def submit(self, submitted_intent):
            self.submit_calls += 1
            raise UnknownSubmission(submitted_intent.idempotency_key)

        async def get_by_client_order_id(self, client_order_id: str):
            raise BrokerLookupUnavailable(client_order_id)

    broker = LookupOutageBroker()
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(BrokerLookupUnavailable):
        await orchestrator.execute(intent.intent_id)
    assert await store.get_state(intent.intent_id) is IntentState.ACK_UNKNOWN

    with pytest.raises(BrokerLookupUnavailable):
        await orchestrator.execute(intent.intent_id)
    assert broker.submit_calls == 1


@pytest.mark.asyncio
async def test_replacement_new_lookup_keeps_hold_and_records_replacement_order(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )

    class ReplacementNewBroker:
        submit_calls = 0

        async def submit(self, submitted_intent):
            self.submit_calls += 1
            raise BrokerReplacementPending(submitted_intent.idempotency_key)

        async def get_by_client_order_id(self, client_order_id: str):
            return BrokerOrder(
                order_id="replacement-order",
                client_order_id=client_order_id,
                status=BrokerOrderStatus.NEW,
            )

    broker = ReplacementNewBroker()
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(BrokerReplacementPending):
        await orchestrator.execute(intent.intent_id)
    order = await orchestrator.execute(intent.intent_id)

    assert order.order_id == "replacement-order"
    assert await store.get_state(intent.intent_id) is IntentState.REPLACEMENT_PENDING
    assert broker.submit_calls == 1


@pytest.mark.asyncio
async def test_replacement_partial_lookup_keeps_hold(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.mark_replacement_pending(intent.intent_id, "broker replacement")

    class PartialReplacementBroker:
        async def submit(self, submitted_intent):
            raise AssertionError("replacement hold must not submit")

        async def get_by_client_order_id(self, client_order_id: str):
            return BrokerOrder(
                order_id="replacement-order",
                client_order_id=client_order_id,
                status=BrokerOrderStatus.PARTIALLY_FILLED,
            )

    await ExecutionOrchestrator(store, PartialReplacementBroker()).execute(intent.intent_id)

    assert await store.get_state(intent.intent_id) is IntentState.REPLACEMENT_PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker_status", "intent_state"),
    [
        (BrokerOrderStatus.CANCELED, IntentState.CANCELED),
        (BrokerOrderStatus.REJECTED, IntentState.REJECTED),
    ],
)
async def test_replacement_terminal_nonfill_releases_risk(
    engine: AsyncEngine,
    broker_status: BrokerOrderStatus,
    intent_state: IntentState,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.mark_replacement_pending(intent.intent_id, "broker replacement")

    class TerminalReplacementBroker:
        async def submit(self, submitted_intent):
            raise AssertionError("replacement hold must not submit")

        async def get_by_client_order_id(self, client_order_id: str):
            return BrokerOrder(
                order_id="replacement-order",
                client_order_id=client_order_id,
                status=broker_status,
            )

    order = await ExecutionOrchestrator(store, TerminalReplacementBroker()).execute(
        intent.intent_id
    )

    assert order.order_id == "replacement-order"
    assert await store.get_state(intent.intent_id) is intent_state
    assert (await store.claim_submission(intent.intent_id)).broker_order_id == "replacement-order"
    replacement = make_intent().model_copy(
        update={"intent_id": "intent-002", "idempotency_key": "intent-002-v1"}
    )
    await store.create_intent(replacement)
    await store.authorize_with_reservation(
        replacement.intent_id, replacement.account_id, Decimal("125"), Decimal("200")
    )


@pytest.mark.asyncio
async def test_replacement_fill_with_new_order_id_consumes_risk(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.mark_replacement_pending(intent.intent_id, "broker replacement")

    class FilledReplacementBroker:
        async def submit(self, submitted_intent):
            raise AssertionError("replacement hold must not submit")

        async def get_by_client_order_id(self, client_order_id: str):
            return BrokerOrder(
                order_id="replacement-order",
                client_order_id=client_order_id,
                status=BrokerOrderStatus.FILLED,
            )

    order = await ExecutionOrchestrator(store, FilledReplacementBroker()).execute(intent.intent_id)

    assert order.order_id == "replacement-order"
    assert await store.get_state(intent.intent_id) is IntentState.RECONCILED
    assert (await store.claim_submission(intent.intent_id)).broker_order_id == "replacement-order"
    replacement = make_intent().model_copy(
        update={"intent_id": "intent-002", "idempotency_key": "intent-002-v1"}
    )
    await store.create_intent(replacement)
    with pytest.raises(CapacityExceeded):
        await store.authorize_with_reservation(
            replacement.intent_id, replacement.account_id, Decimal("125"), Decimal("200")
        )


@pytest.mark.asyncio
async def test_unknown_lookup_miss_resets_claim_for_later_retry() -> None:
    intent = make_intent()
    store = MemoryStore(intent)

    class MissingThenFilledBroker(FakeBroker):
        async def submit(self, submitted_intent):
            if self.submit_calls == 0:
                self.submit_calls += 1
                raise UnknownSubmission(submitted_intent.idempotency_key)
            return await super().submit(submitted_intent)

    broker = MissingThenFilledBroker()
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(RuntimeError, match="submission state remains unknown"):
        await orchestrator.execute(intent.intent_id)
    order = await orchestrator.execute(intent.intent_id)

    assert order.status is BrokerOrderStatus.FILLED
    assert store.reset_reasons == ["broker submission lookup returned no order"]


@pytest.mark.asyncio
async def test_unexpected_submit_error_resets_claim_for_later_retry() -> None:
    intent = make_intent()
    store = MemoryStore(intent)

    class FailsOnceBroker(FakeBroker):
        async def submit(self, submitted_intent):
            if self.submit_calls == 0:
                self.submit_calls += 1
                raise TimeoutError("broker unavailable")
            return await super().submit(submitted_intent)

    broker = FailsOnceBroker()
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(TimeoutError, match="broker unavailable"):
        await orchestrator.execute(intent.intent_id)
    order = await orchestrator.execute(intent.intent_id)

    assert order.status is BrokerOrderStatus.FILLED
    assert store.reset_reasons == ["unexpected broker submit error: broker unavailable"]


@pytest.mark.asyncio
async def test_mismatched_broker_identity_is_not_persisted() -> None:
    intent = make_intent()
    store = MemoryStore(intent)
    broker = FakeBroker()

    async def mismatched_submit(submitted_intent):
        return BrokerOrder(
            order_id="wrong-order",
            client_order_id="different-intent",
            status=BrokerOrderStatus.FILLED,
        )

    broker.submit = mismatched_submit  # type: ignore[method-assign]
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(ValueError, match="client order id does not match intent"):
        await orchestrator.execute(intent.intent_id)

    assert store.recorded == []


@pytest.mark.asyncio
async def test_repeated_terminal_execution_has_explicit_terminal_outcome() -> None:
    intent = make_intent()
    store = MemoryStore(intent, IntentState.REJECTED)
    orchestrator = ExecutionOrchestrator(store, FakeBroker())

    with pytest.raises(RuntimeError, match="already rejected"):
        await orchestrator.execute(intent.intent_id)


@pytest.mark.asyncio
async def test_fake_broker_returns_none_for_unknown_client_order_id() -> None:
    broker = FakeBroker()

    assert await broker.get_by_client_order_id("missing-intent") is None


@pytest.mark.asyncio
async def test_partial_fill_cannot_be_terminally_released(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.record_broker_order(intent.intent_id, "partial-order", IntentState.PARTIALLY_FILLED)

    with pytest.raises(ValueError, match="partially filled.*partial reservation accounting"):
        await store.mark_not_filled(intent.intent_id, IntentState.CANCELED, "cancelled")


@pytest.mark.asyncio
async def test_late_identical_reconciliation_is_idempotent(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.record_broker_order(intent.intent_id, "stable-order", IntentState.RECONCILED)

    await store.record_broker_order(intent.intent_id, "stable-order", IntentState.RECONCILED)

    assert await store.get_state(intent.intent_id) is IntentState.RECONCILED


def test_broker_transition_rejects_partial_fill_regression() -> None:
    with pytest.raises(ValueError, match="cannot regress broker state"):
        validate_broker_transition(
            IntentState.PARTIALLY_FILLED,
            IntentState.SUBMITTING,
            "stable-order",
            "stable-order",
        )


def test_broker_transition_allows_monotonic_progression() -> None:
    validate_broker_transition(
        IntentState.SUBMITTING,
        IntentState.PARTIALLY_FILLED,
        "stable-order",
        "stable-order",
    )
    validate_broker_transition(
        IntentState.PARTIALLY_FILLED,
        IntentState.RECONCILED,
        "stable-order",
        "stable-order",
    )


def test_broker_transition_rejects_conflicting_order_id() -> None:
    with pytest.raises(ValueError, match="different broker order"):
        validate_broker_transition(
            IntentState.SUBMITTING,
            IntentState.PARTIALLY_FILLED,
            "stable-order",
            "other-order",
        )


@pytest.mark.asyncio
async def test_store_rejects_broker_regression_and_conflicting_order(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.record_broker_order(intent.intent_id, "stable-order", IntentState.PARTIALLY_FILLED)

    with pytest.raises(ValueError, match="cannot regress broker state"):
        await store.record_broker_order(intent.intent_id, "stable-order", IntentState.SUBMITTING)
    with pytest.raises(ValueError, match="different broker order"):
        await store.record_broker_order(intent.intent_id, "other-order", IntentState.RECONCILED)


@pytest.mark.asyncio
async def test_store_recovery_only_returns_submitting_to_authorized(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )
    await store.claim_submission(intent.intent_id)
    await store.recover_submission(intent.intent_id, "worker lease expired")

    claim = await store.claim_submission(intent.intent_id)
    assert claim.should_submit is True

    await store.record_broker_order(intent.intent_id, "partial-order", IntentState.PARTIALLY_FILLED)
    with pytest.raises(ValueError, match="cannot reset submission"):
        await store.recover_submission(intent.intent_id, "late worker recovery")


@pytest.mark.asyncio
async def test_unknown_ack_state_never_resubmits_and_uses_reader_lookup(
    engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = Store(sessions)
    intent = make_intent()
    await store.create_intent(intent)
    await store.authorize_with_reservation(
        intent.intent_id, intent.account_id, Decimal("125"), Decimal("200")
    )

    class AmbiguousBroker:
        submit_calls = 0
        lookup_calls = 0

        async def submit(self, submitted_intent):
            self.submit_calls += 1
            raise UnknownSubmission(submitted_intent.idempotency_key)

        async def get_by_client_order_id(self, client_order_id: str):
            self.lookup_calls += 1
            if self.lookup_calls == 1:
                raise BrokerLookupUnavailable(client_order_id)
            return BrokerOrder(
                order_id="order-1",
                client_order_id=client_order_id,
                status=BrokerOrderStatus.FILLED,
            )

    broker = AmbiguousBroker()
    orchestrator = ExecutionOrchestrator(store, broker)

    with pytest.raises(BrokerLookupUnavailable):
        await orchestrator.execute(intent.intent_id)
    order = await orchestrator.execute(intent.intent_id)

    assert order.order_id == "order-1"
    assert broker.submit_calls == 1
    assert broker.lookup_calls == 2
    assert await store.get_state(intent.intent_id) is IntentState.RECONCILED
