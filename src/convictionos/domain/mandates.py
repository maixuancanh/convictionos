from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from convictionos.domain.trading import TradeIntent


class ApprovalMode(StrEnum):
    ASSISTED = "assisted"
    GUARDED = "guarded"
    MANDATED = "mandated"


class KillSwitch(StrEnum):
    OFF = "off"
    FREEZE = "freeze"
    SECURE = "secure"
    UNWIND = "unwind"


class PolicyOutcome(StrEnum):
    DENY = "deny"
    ESCALATE = "escalate"
    ALLOW = "allow"


class Mandate(BaseModel):
    model_config = ConfigDict(frozen=True)

    mandate_id: str
    version: int = Field(gt=0)
    account_id: str
    expires_at: datetime
    approval_mode: ApprovalMode
    allowed_underlyings: frozenset[str]
    max_trade_loss: Decimal = Field(gt=0)
    max_notional: Decimal = Field(gt=0)
    autonomous_notional: Decimal = Field(gt=0)
    required_options_level: int = Field(ge=0, le=3)

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class PolicyContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    now: datetime
    data_fresh: bool
    kill_switch: KillSwitch
    broker_options_level: int = Field(ge=0, le=3)
    projected_notional: Decimal = Field(ge=0)
    projected_max_loss: Decimal = Field(ge=0)

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: PolicyOutcome
    reasons: tuple[str, ...]
    mandate_id: str
    mandate_version: int
    operation_hash: str


def evaluate_policy(
    intent: TradeIntent, mandate: Mandate, context: PolicyContext
) -> PolicyDecision:
    hard_denies: list[str] = []
    if context.kill_switch is not KillSwitch.OFF:
        hard_denies.append("kill_switch_active")
    if context.now >= mandate.expires_at:
        hard_denies.append("mandate_expired")
    if context.now >= intent.expires_at:
        hard_denies.append("intent_expired")
    if not context.data_fresh:
        hard_denies.append("stale_critical_data")
    if intent.account_id != mandate.account_id:
        hard_denies.append("account_not_granted")
    if intent.mandate_id != mandate.mandate_id or intent.mandate_version != mandate.version:
        hard_denies.append("mandate_version_mismatch")
    if intent.underlying not in mandate.allowed_underlyings:
        hard_denies.append("underlying_not_allowed")
    if context.broker_options_level < mandate.required_options_level:
        hard_denies.append("insufficient_broker_options_level")
    if context.projected_max_loss > mandate.max_trade_loss:
        hard_denies.append("max_trade_loss_exceeded")
    if context.projected_notional > mandate.max_notional:
        hard_denies.append("max_notional_exceeded")

    if hard_denies:
        outcome = PolicyOutcome.DENY
        reasons = tuple(hard_denies)
    elif mandate.approval_mode is ApprovalMode.ASSISTED:
        outcome = PolicyOutcome.ESCALATE
        reasons = ("human_approval_required",)
    elif context.projected_notional > mandate.autonomous_notional:
        outcome = PolicyOutcome.ESCALATE
        reasons = ("autonomous_notional_exceeded",)
    else:
        outcome = PolicyOutcome.ALLOW
        reasons = ()

    return PolicyDecision(
        outcome=outcome,
        reasons=reasons,
        mandate_id=mandate.mandate_id,
        mandate_version=mandate.version,
        operation_hash=intent.operation_hash(),
    )
