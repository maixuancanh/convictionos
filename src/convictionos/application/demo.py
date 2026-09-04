from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from convictionos.application.execution import ExecutionOrchestrator
from convictionos.application.intelligence import build_trade_intent, load_case
from convictionos.domain.mandates import (
    ApprovalMode,
    KillSwitch,
    Mandate,
    PolicyContext,
    PolicyDecision,
    PolicyOutcome,
    evaluate_policy,
)
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.trading import IntentState
from convictionos.infrastructure.brokers import BrokerOrderStatus, BrokerPort
from convictionos.infrastructure.store import Store


class DemoService:
    def __init__(self, store: Store, broker: BrokerPort) -> None:
        self._store = store
        self._broker = broker

    @staticmethod
    def mandate(now: datetime) -> Mandate:
        return Mandate(
            mandate_id="mandate-001",
            version=1,
            account_id="paper-account",
            expires_at=now + timedelta(days=30),
            approval_mode=ApprovalMode.GUARDED,
            allowed_underlyings=frozenset({"SPY"}),
            max_trade_loss=Decimal("250"),
            max_notional=Decimal("1000"),
            autonomous_notional=Decimal("200"),
            required_options_level=3,
        )

    async def run(
        self, long_option_symbol: str, short_option_symbol: str, limit_price: Decimal
    ) -> DecisionReceipt:
        case = load_case(Path("fixtures/synthetic_catalyst.json"))
        intent = build_trade_intent(
            case,
            account_id="paper-account",
            mandate_id="mandate-001",
            mandate_version=1,
            long_option_symbol=long_option_symbol,
            short_option_symbol=short_option_symbol,
            limit_price=limit_price,
        )
        now = intent.created_at + timedelta(minutes=1)
        decision = evaluate_policy(
            intent,
            self.mandate(now),
            PolicyContext(
                now=now,
                data_fresh=True,
                kill_switch=KillSwitch.OFF,
                broker_options_level=3,
                projected_notional=intent.max_loss,
                projected_max_loss=intent.max_loss,
            ),
        )
        if decision.outcome is not PolicyOutcome.ALLOW:
            raise RuntimeError(f"demo intent was not allowed: {decision.reasons}")
        await self._store.create_intent(intent)
        existing_receipt = await self._store.find_receipt(intent.intent_id)
        if existing_receipt is not None:
            return existing_receipt
        state = await self._store.get_state(intent.intent_id)
        if state is IntentState.VALIDATED:
            await self._store.authorize_with_reservation(
                intent.intent_id, intent.account_id, intent.max_loss, Decimal("200")
            )
        elif state not in {
            IntentState.AUTHORIZED,
            IntentState.SUBMITTING,
            IntentState.RECONCILED,
        }:
            raise RuntimeError(f"intent cannot continue from state {state.value}")
        order = await ExecutionOrchestrator(self._store, self._broker).execute(intent.intent_id)
        if order.status is not BrokerOrderStatus.FILLED:
            raise RuntimeError("receipt requires a filled and reconciled broker order")
        receipt = DecisionReceipt(
            receipt_id=f"receipt-{intent.intent_id}",
            intent_id=intent.intent_id,
            operation_hash=intent.operation_hash(),
            evidence_snapshot_hash=intent.data_snapshot_hash,
            mandate_id=intent.mandate_id,
            mandate_version=intent.mandate_version,
            policy_outcome=decision.outcome,
            policy_reasons=decision.reasons,
            broker_order_id=order.order_id,
            broker_status=order.status.value,
            previous_receipt_hash="genesis",
        )
        await self._store.save_receipt(receipt)
        return receipt

    def evaluate_mutation(
        self, quantity: Decimal, limit_price: Decimal
    ) -> tuple[PolicyDecision, str, str]:
        case = load_case(Path("fixtures/synthetic_catalyst.json"))
        baseline = build_trade_intent(
            case,
            account_id="paper-account",
            mandate_id="mandate-001",
            mandate_version=1,
            long_option_symbol="SPY280120C00500000",
            short_option_symbol="SPY280120C00510000",
            limit_price=Decimal("1.25"),
        )
        mutated_legs = tuple(leg.model_copy(update={"quantity": quantity}) for leg in baseline.legs)
        intent = baseline.model_copy(
            update={
                "intent_id": f"{baseline.intent_id}-mutation",
                "idempotency_key": f"{baseline.idempotency_key}-mutation",
                "legs": mutated_legs,
                "limit_price": limit_price,
                "max_loss": (limit_price * quantity * Decimal("100")).quantize(Decimal("0.01")),
            }
        )
        now = datetime(2026, 8, 29, 14, 1, tzinfo=UTC)
        decision = evaluate_policy(
            intent,
            self.mandate(now),
            PolicyContext(
                now=now,
                data_fresh=True,
                kill_switch=KillSwitch.OFF,
                broker_options_level=3,
                projected_notional=intent.max_loss,
                projected_max_loss=intent.max_loss,
            ),
        )
        return decision, baseline.operation_hash(), intent.operation_hash()
