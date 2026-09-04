from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from convictionos.application.demo import DemoService
from convictionos.application.intelligence import load_synthetic_case
from convictionos.domain.evidence import SyntheticNarrativeCase
from convictionos.domain.mandates import PolicyOutcome
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.trading import Horizon, IntentState, TradeIntent
from convictionos.infrastructure.store import Store

_SYNTHETIC_INTENT_ID = "intent-synthetic-catalyst-001"
_EXECUTION_MODES = {
    "fake": "FAKE DEMO · PAPER-SAFE",
    "alpaca_paper": "ALPACA PAPER · NO LIVE CAPITAL",
}


def _execution_mode(broker_mode: str) -> str:
    return _EXECUTION_MODES.get(broker_mode, "UNAVAILABLE")


class LatestDecisionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str
    underlying: str
    horizon: Horizon
    thesis: str
    max_loss: Decimal
    policy_outcome: PolicyOutcome
    policy_reasons: tuple[str, ...]
    intent_state: IntentState
    broker_order_id: str
    receipt_hash: str
    evidence_snapshot_hash: str
    mandate_id: str
    mandate_version: int
    risk_reserved: Decimal
    broker_status: str
    receipt_verified: bool
    intent_data_snapshot_hash: str


class ControlPlaneSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_mode: str
    live_trading_authorized: bool
    case_id: str
    synthetic: bool
    underlying: str
    horizon: Horizon
    thesis: str
    invalidation: str
    evidence_snapshot_hash: str
    evidence_count: int
    mandate_id: str
    mandate_version: int
    mandate_max_trade_loss: Decimal
    mandate_max_notional: Decimal
    mandate_autonomous_notional: Decimal
    risk_reserved: Decimal | None
    broker_status: str | None
    evidence_claim: str
    evidence_source_id: str
    evidence_event_at: datetime
    evidence_observed_at: datetime
    evidence_ingested_at: datetime
    evidence_source_checksum: str
    latest_decision: LatestDecisionView | None

    @classmethod
    def empty(cls, case: SyntheticNarrativeCase, broker_mode: str) -> "ControlPlaneSummary":
        return cls(
            execution_mode=_execution_mode(broker_mode),
            live_trading_authorized=False,
            case_id=case.case_id,
            synthetic=case.synthetic,
            underlying=case.underlying,
            horizon=case.horizon,
            thesis=case.thesis,
            invalidation=case.invalidation,
            evidence_snapshot_hash=case.snapshot_hash(),
            evidence_count=len(case.evidence),
            mandate_id="mandate-001",
            mandate_version=1,
            mandate_max_trade_loss=Decimal("250"),
            mandate_max_notional=Decimal("1000"),
            mandate_autonomous_notional=Decimal("200"),
            risk_reserved=None,
            broker_status=None,
            evidence_claim=case.evidence[0].claim,
            evidence_source_id=case.evidence[0].source_id,
            evidence_event_at=case.evidence[0].event_at,
            evidence_observed_at=case.evidence[0].observed_at,
            evidence_ingested_at=case.evidence[0].ingested_at,
            evidence_source_checksum=case.evidence[0].source_checksum,
            latest_decision=None,
        )

    @classmethod
    def from_receipt(
        cls,
        case: SyntheticNarrativeCase,
        intent: TradeIntent,
        state: IntentState,
        receipt: DecisionReceipt,
        broker_mode: str,
        risk_reserved: Decimal,
    ) -> "ControlPlaneSummary":
        return cls(
            execution_mode=_execution_mode(broker_mode),
            live_trading_authorized=False,
            case_id=case.case_id,
            synthetic=case.synthetic,
            underlying=case.underlying,
            horizon=case.horizon,
            thesis=case.thesis,
            invalidation=case.invalidation,
            evidence_snapshot_hash=case.snapshot_hash(),
            evidence_count=len(case.evidence),
            mandate_id=intent.mandate_id,
            mandate_version=intent.mandate_version,
            mandate_max_trade_loss=DemoService.mandate(case.created_at).max_trade_loss,
            mandate_max_notional=DemoService.mandate(case.created_at).max_notional,
            mandate_autonomous_notional=DemoService.mandate(case.created_at).autonomous_notional,
            risk_reserved=risk_reserved,
            broker_status=receipt.broker_status,
            evidence_claim=case.evidence[0].claim,
            evidence_source_id=case.evidence[0].source_id,
            evidence_event_at=case.evidence[0].event_at,
            evidence_observed_at=case.evidence[0].observed_at,
            evidence_ingested_at=case.evidence[0].ingested_at,
            evidence_source_checksum=case.evidence[0].source_checksum,
            latest_decision=LatestDecisionView(
                intent_id=intent.intent_id,
                underlying=intent.underlying,
                horizon=intent.horizon,
                thesis=case.thesis,
                max_loss=intent.max_loss,
                policy_outcome=receipt.policy_outcome,
                policy_reasons=receipt.policy_reasons,
                intent_state=state,
                broker_order_id=receipt.broker_order_id,
                receipt_hash=receipt.receipt_hash(),
                evidence_snapshot_hash=receipt.evidence_snapshot_hash,
                mandate_id=receipt.mandate_id,
                mandate_version=receipt.mandate_version,
                risk_reserved=risk_reserved,
                broker_status=receipt.broker_status,
                receipt_verified=True,
                intent_data_snapshot_hash=intent.data_snapshot_hash,
            ),
        )


class ControlPlaneService:
    def __init__(self, store: Store, *, broker_mode: str) -> None:
        self._store = store
        self._broker_mode = broker_mode

    async def summary(self) -> ControlPlaneSummary:
        case = load_synthetic_case()
        receipt = await self._store.find_receipt(_SYNTHETIC_INTENT_ID)
        if receipt is None:
            return ControlPlaneSummary.empty(case, self._broker_mode)
        intent = await self._store.load_intent(receipt.intent_id)
        if receipt.operation_hash != intent.operation_hash():
            raise ValueError("receipt operation hash mismatch")
        if receipt.evidence_snapshot_hash != case.snapshot_hash():
            raise ValueError("receipt evidence snapshot hash mismatch")
        if intent.data_snapshot_hash != case.snapshot_hash():
            raise ValueError("intent evidence snapshot hash mismatch")
        state = await self._store.get_state(receipt.intent_id)
        reservation = await self._store.find_reservation(receipt.intent_id)
        if reservation is None:
            raise ValueError("risk reservation missing")
        return ControlPlaneSummary.from_receipt(
            case, intent, state, receipt, self._broker_mode, Decimal(str(reservation.amount))
        )
