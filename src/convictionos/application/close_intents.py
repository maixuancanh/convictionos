import re
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from convictionos.application.exit_policy import ExitDecision, ExitOutcome
from convictionos.domain.positions import ManagedOptionPosition, PositionLifecycleState
from convictionos.domain.trading import (
    InstrumentKind,
    OrderType,
    PositionIntent,
    Side,
    TradeIntent,
    TradeLeg,
)


class CloseIntentDenial(StrEnum):
    NOT_CLOSE_DECISION = "not_close_decision"
    MISSING_CLOSE_CREDIT = "missing_close_credit"
    INVALID_POSITION_STATE = "invalid_position_state"
    POSITION_HASH_MISMATCH = "position_hash_mismatch"
    SHAPE_MISMATCH = "shape_mismatch"
    EXPIRED_OBSERVATION = "expired_observation"


class CloseAuthorizationDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authorized: bool
    reasons: tuple[str, ...] = ()

    @property
    def primary_reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None


class CloseIntentFactory:
    _EXPIRY = timedelta(minutes=5)

    @classmethod
    def create(
        cls,
        position: ManagedOptionPosition,
        decision: ExitDecision,
        now: datetime,
    ) -> TradeIntent:
        authorization = cls._authorize_inputs(position, decision, now)
        if not authorization.authorized:
            raise ValueError(authorization.primary_reason or "close_intent_denied")
        assert decision.close_credit is not None
        return TradeIntent(
            intent_id=f"{position.position_id}-close-v1",
            idempotency_key=f"{position.position_id}-close-v1",
            account_id=position.account_id,
            mandate_id=position.opening_intent_id,
            mandate_version=1,
            created_at=now,
            expires_at=now + cls._EXPIRY,
            horizon=position.horizon,
            underlying=re.split(r"\d", position.long_leg.symbol, maxsplit=1)[0],
            thesis_ref=position.opening_thesis_ref,
            legs=(
                TradeLeg(
                    symbol=position.long_leg.symbol,
                    kind=InstrumentKind.US_OPTION,
                    side=Side.SELL,
                    position_intent=PositionIntent.SELL_TO_CLOSE,
                    quantity=Decimal(position.units),
                    ratio_quantity=position.long_leg.ratio_quantity,
                ),
                TradeLeg(
                    symbol=position.short_leg.symbol,
                    kind=InstrumentKind.US_OPTION,
                    side=Side.BUY,
                    position_intent=PositionIntent.BUY_TO_CLOSE,
                    quantity=Decimal(position.units),
                    ratio_quantity=position.short_leg.ratio_quantity,
                ),
            ),
            order_type=OrderType.LIMIT,
            limit_price=decision.close_credit,
            max_loss=position.entry_basis_provenance.authorized_cost_ceiling,
            exit_plan=decision.primary_reason,
            data_snapshot_hash=decision.observation_hash,
            position_id=position.position_id,
            exit_decision_hash=decision.decision_hash,
            position_state_hash=decision.position_state_hash,
        )

    @classmethod
    def _authorize_inputs(
        cls, position: ManagedOptionPosition, decision: ExitDecision, now: datetime
    ) -> CloseAuthorizationDecision:
        reasons: list[str] = []
        if decision.outcome is not ExitOutcome.CLOSE:
            reasons.append(CloseIntentDenial.NOT_CLOSE_DECISION.value)
        if decision.close_credit is None or decision.close_credit <= 0:
            reasons.append(CloseIntentDenial.MISSING_CLOSE_CREDIT.value)
        if position.state is not PositionLifecycleState.OPEN:
            reasons.append(CloseIntentDenial.INVALID_POSITION_STATE.value)
        if decision.position_state_hash != _position_state_hash(position):
            reasons.append(CloseIntentDenial.POSITION_HASH_MISMATCH.value)
        if decision.observation_hash == "":
            reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
        if now.tzinfo is None or now.utcoffset() is None:
            reasons.append(CloseIntentDenial.EXPIRED_OBSERVATION.value)
        return CloseAuthorizationDecision(authorized=not reasons, reasons=tuple(reasons))


def authorize_close(
    position: ManagedOptionPosition,
    intent: TradeIntent,
    decision: ExitDecision,
) -> CloseAuthorizationDecision:
    reasons: list[str] = []
    expected = CloseIntentFactory._authorize_inputs(position, decision, intent.created_at)
    reasons.extend(expected.reasons)
    if intent.position_id != position.position_id:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.exit_decision_hash != decision.decision_hash:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.position_state_hash != _position_state_hash(position):
        reasons.append(CloseIntentDenial.POSITION_HASH_MISMATCH.value)
    expected_symbols = {position.long_leg.symbol, position.short_leg.symbol}
    if {leg.symbol for leg in intent.legs} != expected_symbols or len(intent.legs) != 2:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if any(leg.kind is not InstrumentKind.US_OPTION for leg in intent.legs):
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if any(
        leg.quantity != Decimal(position.units) or leg.ratio_quantity != 1
        for leg in intent.legs
    ):
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.limit_price != decision.close_credit:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    expected_legs = (
        (position.long_leg.symbol, Side.SELL, PositionIntent.SELL_TO_CLOSE),
        (position.short_leg.symbol, Side.BUY, PositionIntent.BUY_TO_CLOSE),
    )
    actual_legs = tuple(
        (leg.symbol, leg.side, leg.position_intent) for leg in intent.legs
    )
    if actual_legs != expected_legs:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.underlying != re.split(r"\d", position.long_leg.symbol, maxsplit=1)[0]:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.horizon is not position.horizon or intent.account_id != position.account_id:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.data_snapshot_hash != decision.observation_hash:
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.idempotency_key != f"{position.position_id}-close-v1":
        reasons.append(CloseIntentDenial.SHAPE_MISMATCH.value)
    if intent.expires_at != intent.created_at + CloseIntentFactory._EXPIRY:
        reasons.append(CloseIntentDenial.EXPIRED_OBSERVATION.value)
    return CloseAuthorizationDecision(authorized=not reasons, reasons=tuple(dict.fromkeys(reasons)))


def _position_state_hash(position: ManagedOptionPosition) -> str:
    from convictionos.domain.canonical import sha256_hex

    return sha256_hex(
        {
            "snapshot": position.position_snapshot_hash,
            "state": position.state.value,
            "version": position.version,
        }
    )
