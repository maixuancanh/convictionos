from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.positions import ManagedOptionPosition
from convictionos.domain.strategy import OptionMarketQuote
from convictionos.domain.trading import Horizon

EXIT_POLICY_VERSION = "exit-policy-v1"
_FRESHNESS = timedelta(minutes=5)
_OPTION_MULTIPLIER = Decimal("100")


class ExitOutcome(StrEnum):
    HOLD = "hold"
    CLOSE = "close"
    REVIEW_REQUIRED = "review_required"


class ExitPolicyPreset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["exit-policy-v1"] = "exit-policy-v1"
    horizon: Horizon
    take_profit_return: Decimal
    stop_loss_return: Decimal
    expiry_buffer_dte: int = Field(ge=0)
    max_holding_days: int = Field(gt=0)


_PRESETS: dict[Horizon, ExitPolicyPreset] = {
    Horizon.CATALYST: ExitPolicyPreset(
        horizon=Horizon.CATALYST,
        take_profit_return=Decimal("0.40"),
        stop_loss_return=Decimal("-0.35"),
        expiry_buffer_dte=2,
        max_holding_days=10,
    ),
    Horizon.SWING: ExitPolicyPreset(
        horizon=Horizon.SWING,
        take_profit_return=Decimal("0.50"),
        stop_loss_return=Decimal("-0.40"),
        expiry_buffer_dte=7,
        max_holding_days=56,
    ),
    Horizon.THEMATIC: ExitPolicyPreset(
        horizon=Horizon.THEMATIC,
        take_profit_return=Decimal("0.60"),
        stop_loss_return=Decimal("-0.50"),
        expiry_buffer_dte=21,
        max_holding_days=365,
    ),
}


def exit_policy_preset(horizon: Horizon) -> ExitPolicyPreset:
    return _PRESETS[horizon]


class CloseQuoteObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str = Field(min_length=1)
    long_quote: OptionMarketQuote | None = None
    short_quote: OptionMarketQuote | None = None
    observed_at: datetime
    snapshot_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observation(self) -> "CloseQuoteObservation":
        _require_aware(self.observed_at)
        if self.long_quote is not None:
            _require_aware(self.long_quote.as_of)
        if self.short_quote is not None:
            _require_aware(self.short_quote.as_of)
        if self.snapshot_hash != self.content_hash():
            raise ValueError("snapshot hash does not match close quote payload")
        return self

    def content_hash(self) -> str:
        return sha256_hex(
            {
                "underlying": self.underlying,
                "observed_at": self.observed_at,
                "long_quote": _quote_payload(self.long_quote),
                "short_quote": _quote_payload(self.short_quote),
            }
        )


class ExitSignalObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str = Field(min_length=1)
    ai_direction: ThesisDirection
    quant_direction: ThesisDirection
    ai_valid: bool
    quant_valid: bool
    observed_at: datetime
    snapshot_hash: str = Field(min_length=1)
    signal_content_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observation(self) -> "ExitSignalObservation":
        _require_aware(self.observed_at)
        if self.signal_content_hash != self.content_hash():
            raise ValueError("signal content hash does not match payload")
        return self

    def content_hash(self) -> str:
        return sha256_hex(
            self.model_dump(mode="python", exclude={"signal_content_hash"})
        )


class ExitEvaluationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    position: ManagedOptionPosition
    now: datetime
    opened_at: datetime
    close_quotes: CloseQuoteObservation
    signals: ExitSignalObservation | None = None
    thesis_invalidated: bool = False
    broker_event: str | None = None
    broker_position_match: bool = True

    @model_validator(mode="after")
    def validate_datetimes(self) -> "ExitEvaluationContext":
        _require_aware(self.now)
        _require_aware(self.opened_at)
        if self.signals is not None:
            _require_aware(self.signals.observed_at)
        return self


class ExitDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ExitOutcome
    primary_reason: str
    reasons: tuple[str, ...]
    basis_source: Literal["broker_fill", "authorized_ceiling"]
    entry_basis: Decimal
    close_credit: Decimal | None
    performance_eligible: Literal[False] = False
    policy_version: Literal["exit-policy-v1"]
    observation_hash: str
    position_state_hash: str
    decision_hash: str

    @classmethod
    def compute_hash(cls, decision: "ExitDecision") -> str:
        return sha256_hex(decision.model_dump(mode="python", exclude={"decision_hash"}))

    @model_validator(mode="after")
    def validate_decision_hash(self) -> "ExitDecision":
        if self.decision_hash != self.compute_hash(self):
            raise ValueError("decision hash does not match payload")
        return self


class ExitPolicyEngine:
    def evaluate(self, context: ExitEvaluationContext) -> ExitDecision:
        position = context.position
        preset = exit_policy_preset(position.horizon)
        basis, basis_source = self._entry_basis(position)
        quote_valid, close_credit = self._close_credit(position, context)
        reasons: list[str] = []
        review = False

        if position.exit_policy_version != preset.version:
            reasons.append("unsupported_exit_policy_version")
            review = True

        if context.broker_event is not None or not context.broker_position_match:
            reasons.append("broker_event_or_position_mismatch")
            review = True
        expiry = (
            position.expiration - context.now.astimezone(UTC).date()
        ).days <= preset.expiry_buffer_dte
        if expiry:
            reasons.append("expiry_buffer_reached")
            if not quote_valid:
                review = True
        if context.thesis_invalidated:
            reasons.append("structured_thesis_invalidation")
        if self._joint_reversal(context):
            reasons.append("joint_reversal_confirmed")

        return_reason: str | None = None
        if basis > 0 and quote_valid and close_credit is not None:
            return_ratio = (close_credit - basis) / basis
            if return_ratio <= preset.stop_loss_return:
                reasons.append("stop_loss_reached")
            if return_ratio >= preset.take_profit_return:
                reasons.append("take_profit_reached")
        max_hold = context.now - context.opened_at >= timedelta(days=preset.max_holding_days)
        if max_hold:
            reasons.append("max_holding_time_reached")

        if reasons:
            return_reason = reasons[0]
        if return_reason is None:
            if expiry and not quote_valid:
                return_reason = "expiry_buffer_reached"
                review = True
            elif not quote_valid:
                return_reason = "close_quote_unavailable"
            else:
                return_reason = "no_exit_trigger"

        if review:
            outcome = ExitOutcome.REVIEW_REQUIRED
        elif return_reason in {"broker_event_or_position_mismatch", "expiry_buffer_reached"}:
            outcome = (
                ExitOutcome.REVIEW_REQUIRED
                if return_reason.startswith("broker")
                else ExitOutcome.CLOSE
            )
        elif return_reason == "no_exit_trigger" or return_reason == "close_quote_unavailable":
            outcome = ExitOutcome.HOLD
        else:
            outcome = ExitOutcome.CLOSE if quote_valid else ExitOutcome.HOLD

        if outcome is ExitOutcome.CLOSE and not quote_valid:
            outcome = ExitOutcome.HOLD
            return_reason = "close_quote_unavailable"

        observation_hash = context.close_quotes.snapshot_hash
        if context.signals is not None:
            observation_hash = sha256_hex(
                {
                    "close": context.close_quotes.snapshot_hash,
                    "signals": context.signals.snapshot_hash,
                    "signal_content": context.signals.signal_content_hash,
                }
            )
        position_state_hash = sha256_hex(
            {
                "snapshot": position.position_snapshot_hash,
                "state": position.state.value,
                "version": position.version,
            }
        )
        decision_hash = sha256_hex(
            {
                "outcome": outcome,
                "primary_reason": return_reason,
                "reasons": tuple(reasons),
                "basis_source": basis_source,
                "entry_basis": basis,
                "close_credit": close_credit if quote_valid else None,
                "performance_eligible": False,
                "policy_version": preset.version,
                "observation_hash": observation_hash,
                "position_state_hash": position_state_hash,
            }
        )
        return ExitDecision(
            outcome=outcome,
            primary_reason=return_reason,
            reasons=tuple(reasons),
            basis_source=basis_source,
            entry_basis=basis,
            close_credit=close_credit if quote_valid else None,
            policy_version=preset.version,
            observation_hash=observation_hash,
            position_state_hash=position_state_hash,
            decision_hash=decision_hash,
        )

    @staticmethod
    def _entry_basis(
        position: ManagedOptionPosition,
    ) -> tuple[Decimal, Literal["broker_fill", "authorized_ceiling"]]:
        accounting = position.entry_basis_provenance
        broker_valid = (
            accounting.broker_reported_fill_price.is_finite()
            and accounting.broker_reported_cost_basis.is_finite()
            and accounting.broker_reported_fill_price > 0
            and accounting.broker_reported_filled_qty == Decimal(position.units)
            and accounting.broker_reported_cost_basis
            == accounting.broker_reported_fill_price * _OPTION_MULTIPLIER * Decimal(position.units)
        )
        if broker_valid:
            return accounting.broker_reported_fill_price, "broker_fill"
        return (
            accounting.authorized_cost_ceiling / (_OPTION_MULTIPLIER * Decimal(position.units)),
            "authorized_ceiling",
        )

    @staticmethod
    def _close_credit(
        position: ManagedOptionPosition, context: ExitEvaluationContext
    ) -> tuple[bool, Decimal | None]:
        observation = context.close_quotes
        long_quote, short_quote = observation.long_quote, observation.short_quote
        if long_quote is None or short_quote is None:
            return False, None
        if (
            long_quote.symbol != position.long_leg.symbol
            or short_quote.symbol != position.short_leg.symbol
            or observation.underlying != position.long_leg.symbol[:-15]
            or long_quote.as_of != short_quote.as_of
            or long_quote.as_of != observation.observed_at
            or long_quote.as_of > context.now
            or context.now - long_quote.as_of > _FRESHNESS
            or not long_quote.tradable
            or not short_quote.tradable
            or long_quote.bid <= 0
            or short_quote.bid <= 0
            or short_quote.ask <= 0
            or long_quote.ask < long_quote.bid
            or short_quote.ask < short_quote.bid
        ):
            return False, None
        credit = long_quote.bid - short_quote.ask
        return credit > 0, credit

    @staticmethod
    def _joint_reversal(context: ExitEvaluationContext) -> bool:
        signal = context.signals
        if signal is None:
            return False
        if (
            signal.underlying != context.close_quotes.underlying
            or signal.observed_at != context.close_quotes.observed_at
            or signal.snapshot_hash != context.close_quotes.snapshot_hash
            or context.now < signal.observed_at
            or context.now - signal.observed_at > _FRESHNESS
            or not signal.ai_valid
            or not signal.quant_valid
            or signal.ai_direction is ThesisDirection.NEUTRAL
            or signal.quant_direction is ThesisDirection.NEUTRAL
            or signal.ai_direction is not signal.quant_direction
        ):
            return False
        return signal.ai_direction is not context.position.direction


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


def _quote_payload(quote: OptionMarketQuote | None) -> dict[str, object] | None:
    if quote is None:
        return None
    payload = quote.model_dump(mode="python")
    payload["expiration"] = quote.expiration.isoformat()
    return payload
