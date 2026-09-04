from datetime import UTC, datetime
from decimal import Decimal

import pytest

from convictionos.application.exit_policy import ExitPolicyEngine
from convictionos.application.position_lifecycle import PositionLifecycleService
from convictionos.infrastructure.alpaca_market import PaperPosition
from tests.application.test_exit_policy import observation, signal
from tests.domain.test_positions import make_position

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class JourneyStore:
    def __init__(self):
        self.position = make_position()
        self.close_intents = []

    async def list_managed_positions(self, states=None):
        return [self.position]

    async def find_unmanaged_reconciled_openings(self, account_id, broker_positions):
        return ()

    async def attach_close_intent(self, position_id, intent, transition, expected_version):
        self.close_intents.append(intent)
        self.position = self.position.transition(
            transition_id=transition.transition_id,
            to_state=transition.to_state,
            reason=transition.reason,
            observed_at=transition.observed_at,
            evidence_hash=transition.evidence_hash,
            close_intent_id=transition.close_intent_id,
            close_operation_hash=transition.close_operation_hash,
        )
        return self.position

    async def transition_position(self, position, transition, expected_version):
        self.position = self.position.transition(
            transition_id=transition.transition_id,
            to_state=transition.to_state,
            reason=transition.reason,
            observed_at=transition.observed_at,
            evidence_hash=transition.evidence_hash,
        )
        return self.position


class JourneyBroker:
    def __init__(self, position):
        self.positions = [
            PaperPosition(
                symbol=leg.symbol,
                quantity=Decimal("1"),
                market_value=Decimal("1"),
                unrealized_pl=Decimal("0"),
            )
            for leg in (position.long_leg, position.short_leg)
        ]

    async def get_positions(self):
        return tuple(self.positions)


class JourneyExit:
    async def evaluate(self, position, *, now):
        quotes = observation(position=position, now=now, observed_at=now)
        context = quotes.model_copy(
            update={
                "signals": signal(
                    snapshot_hash=quotes.close_quotes.snapshot_hash,
                    observed_at=now,
                )
            }
        )
        return ExitPolicyEngine().evaluate(context)


class JourneyExecution:
    def __init__(self, broker):
        self.broker = broker
        self.submissions = 0

    async def execute(self, intent_id):
        self.submissions += 1
        self.broker.positions = []


@pytest.mark.asyncio
async def test_open_to_close_journey_is_idempotent_and_requires_absence_after_fill() -> None:
    store = JourneyStore()
    broker = JourneyBroker(store.position)
    execution = JourneyExecution(broker)
    service = PositionLifecycleService(
        store,
        broker,
        "paper-account",
        exit_decisions=JourneyExit(),
        close_execution=execution,
    )

    first = await service.run_cycle(now=NOW, allow_close_submission=True)
    assert first.transitioned_public_ids == ("position-001",)
    assert first.close_submissions == 1
    assert len(store.close_intents) == 1
    assert store.position.state.value == "close_pending"

    second = await service.run_cycle(now=NOW, allow_close_submission=True)
    assert second.close_submissions == 0
    assert execution.submissions == 1

    store.position = store.position.transition(
        transition_id="filled",
        to_state="close_filled",
        reason="exact parent fill",
        observed_at=NOW,
        evidence_hash="fill-evidence",
    )
    third = await service.run_cycle(now=NOW, allow_close_submission=False)
    assert third.transitioned_public_ids == ("position-001",)
    assert store.position.state.value == "closed_reconciled"
