import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.trading import (
    Horizon,
    InstrumentKind,
    PositionIntent,
    Side,
    TradeLeg,
)

_OCC_SYMBOL = re.compile(r"^[A-Z]{1,6}(?P<expiry>\d{6})(?P<option_type>[CP])(?P<strike>\d{8})$")


class PositionLifecycleState(StrEnum):
    OPEN = "open"
    CLOSE_PENDING = "close_pending"
    CLOSE_FILLED = "close_filled"
    CLOSED_RECONCILED = "closed_reconciled"
    EXPIRATION_REVIEW = "expiration_review"
    ASSIGNMENT_REVIEW = "assignment_review"
    RECONCILIATION_FAILED = "reconciliation_failed"


class PositionTransition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    transition_id: str = Field(min_length=1)
    position_id: str = Field(min_length=1)
    from_state: PositionLifecycleState
    to_state: PositionLifecycleState
    reason: str = Field(min_length=1)
    observed_at: datetime
    evidence_hash: str = Field(min_length=1)
    previous_transition_hash: str = ""
    position_snapshot_hash: str = Field(min_length=1)
    close_intent_id: str | None = Field(default=None, min_length=1)
    close_operation_hash: str | None = Field(default=None, min_length=1)
    close_receipt_hash: str | None = Field(default=None, min_length=1)
    integrity_hash: str

    @classmethod
    def create(
        cls,
        *,
        transition_id: str,
        position_id: str,
        from_state: PositionLifecycleState | str,
        to_state: PositionLifecycleState | str,
        reason: str,
        observed_at: datetime | str,
        evidence_hash: str,
        previous_transition_hash: str = "",
        position_snapshot_hash: str,
        close_intent_id: str | None = None,
        close_operation_hash: str | None = None,
        close_receipt_hash: str | None = None,
    ) -> "PositionTransition":
        raw_payload = {
            "transition_id": transition_id,
            "position_id": position_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
            "observed_at": observed_at,
            "evidence_hash": evidence_hash,
            "previous_transition_hash": previous_transition_hash,
            "position_snapshot_hash": position_snapshot_hash,
            "close_intent_id": close_intent_id,
            "close_operation_hash": close_operation_hash,
            "close_receipt_hash": close_receipt_hash,
        }
        normalized_payload: dict[str, Any] = {
            name: TypeAdapter(field.annotation).validate_python(raw_payload[name])
            for name, field in cls.model_fields.items()
            if name != "integrity_hash" and name in raw_payload
        }
        normalized = cls.model_construct(**normalized_payload)
        integrity_hash = sha256_hex(
            normalized.model_dump(
                mode="python",
                exclude={"integrity_hash"},
            )
        )
        return cls.model_validate({**normalized_payload, "integrity_hash": integrity_hash})

    @model_validator(mode="after")
    def validate_transition(self) -> "PositionTransition":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        expected_hash = self._computed_transition_hash()
        if self.integrity_hash != expected_hash:
            raise ValueError("transition hash does not match payload")
        return self

    def transition_hash(self) -> str:
        return self.integrity_hash

    def _computed_transition_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="python", exclude={"integrity_hash"}))


class ManagedOptionPosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    opening_intent_id: str = Field(min_length=1)
    opening_operation_hash: str = Field(min_length=1)
    opening_receipt_hash: str = Field(min_length=1)
    horizon: Horizon
    direction: ThesisDirection
    long_leg: TradeLeg
    short_leg: TradeLeg
    units: int = Field(gt=0)
    opening_thesis_ref: str = Field(default="opening-thesis-unavailable", min_length=1)
    thesis_invalidation: str = Field(min_length=1)
    entry_basis_provenance: PaperExecutionAccounting
    exit_policy_version: str = Field(min_length=1)
    state: PositionLifecycleState = PositionLifecycleState.OPEN
    version: int = Field(default=0, ge=0)
    close_intent_id: str | None = None
    close_operation_hash: str | None = None
    close_receipt_hash: str | None = None
    position_snapshot_hash: str = Field(min_length=1)
    transitions: tuple[PositionTransition, ...] = ()

    @property
    def expiration(self) -> date:
        return _parse_expiration(self.long_leg.symbol)

    @model_validator(mode="before")
    @classmethod
    def require_serialized_transition_hashes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for transition in data.get("transitions", ()):
                if isinstance(transition, dict) and not transition.get("integrity_hash"):
                    raise ValueError("integrity_hash is required during deserialization")
        return data

    @model_validator(mode="after")
    def validate_position(self) -> "ManagedOptionPosition":
        if (
            self.long_leg.kind is not InstrumentKind.US_OPTION
            or self.short_leg.kind is not InstrumentKind.US_OPTION
        ):
            raise ValueError("position symbols must be US option symbols")
        if _parse_expiration(self.long_leg.symbol) != _parse_expiration(self.short_leg.symbol):
            raise ValueError("option legs must share expiration")
        long_contract = _parse_contract(self.long_leg.symbol)
        short_contract = _parse_contract(self.short_leg.symbol)
        if long_contract[1] != short_contract[1]:
            raise ValueError("option legs must share option type")
        if self.direction is ThesisDirection.NEUTRAL:
            raise ValueError("vertical direction must be bullish or bearish")
        if self.direction is ThesisDirection.BULLISH and long_contract[1] != "C":
            raise ValueError("bullish vertical requires call options")
        if self.direction is ThesisDirection.BEARISH and long_contract[1] != "P":
            raise ValueError("bearish vertical requires put options")
        if self.direction is ThesisDirection.BULLISH and long_contract[2] >= short_contract[2]:
            raise ValueError("bullish vertical requires long strike below short strike")
        if self.direction is ThesisDirection.BEARISH and long_contract[2] <= short_contract[2]:
            raise ValueError("bearish vertical requires long strike above short strike")
        if _underlying(self.long_leg.symbol) != _underlying(self.short_leg.symbol):
            raise ValueError("option legs must share underlying symbols")
        if self.long_leg.symbol == self.short_leg.symbol:
            raise ValueError("option leg symbols must be distinct")
        if self.long_leg.position_intent is not PositionIntent.BUY_TO_OPEN:
            raise ValueError("long leg must be opening BUY_TO_OPEN")
        if self.short_leg.position_intent is not PositionIntent.SELL_TO_OPEN:
            raise ValueError("short leg must be opening SELL_TO_OPEN")
        if self.long_leg.side is not Side.BUY or self.short_leg.side is not Side.SELL:
            raise ValueError("opening option leg side is invalid")
        if self.long_leg.ratio_quantity != self.short_leg.ratio_quantity:
            raise ValueError("option leg ratios must match")
        if self.long_leg.ratio_quantity != 1:
            raise ValueError("vertical option leg ratios must be 1:1")
        if self.long_leg.quantity != self.units or self.short_leg.quantity != self.units:
            raise ValueError("leg quantity must equal units")
        if self.entry_basis_provenance.spread_units != self.units:
            raise ValueError("units must equal entry spread_units")
        accounting = self.entry_basis_provenance
        if accounting.broker_reported_cost_basis > accounting.authorized_cost_ceiling:
            raise ValueError("broker opening debit exceeds authorized debit")
        if accounting.authorized_cost_ceiling != (
            accounting.authorized_limit_price * 100 * accounting.spread_units
        ):
            raise ValueError("authorized cost ceiling is inconsistent")
        if accounting.broker_reported_cost_basis != (
            accounting.broker_reported_fill_price * 100 * accounting.broker_reported_filled_qty
        ):
            raise ValueError("broker reported cost basis is inconsistent")
        if accounting.broker_reported_filled_qty != Decimal(self.units):
            raise ValueError("broker reported filled quantity must equal position units")
        if accounting.broker_reported_filled_qty > accounting.spread_units:
            raise ValueError("broker reported filled quantity exceeds spread units")
        if accounting.broker_reported_fill_price > accounting.authorized_limit_price:
            raise ValueError("broker reported fill price exceeds authorized limit")
        if not self.thesis_invalidation.strip():
            raise ValueError("thesis invalidation must be non-empty")
        expected_snapshot_hash = self._computed_position_snapshot_hash()
        if self.position_snapshot_hash != expected_snapshot_hash:
            raise ValueError("position snapshot hash does not match immutable opening shape")
        if self.version != len(self.transitions):
            raise ValueError("position version must match transition count")
        if not self.transitions and self.state is not PositionLifecycleState.OPEN:
            raise ValueError("non-open position requires a transition ledger")
        transition_ids = [transition.transition_id for transition in self.transitions]
        if len(transition_ids) != len(set(transition_ids)):
            raise ValueError("transition IDs must be unique")
        ledger_refs: list[str | None] = [None, None, None]
        for index, transition in enumerate(self.transitions):
            if transition.position_id != self.position_id:
                raise ValueError("transition belongs to another position")
            if transition.position_snapshot_hash != self.position_snapshot_hash:
                raise ValueError("transition does not match position snapshot binding")
            if transition.integrity_hash != transition._computed_transition_hash():
                raise ValueError("transition hash does not match payload")
            if index == 0:
                if transition.previous_transition_hash:
                    raise ValueError("first transition previous hash must be empty")
                if transition.from_state is not PositionLifecycleState.OPEN:
                    raise ValueError("first transition must start from OPEN")
            else:
                previous = self.transitions[index - 1]
                if previous.to_state in _TERMINAL_OR_REVIEW:
                    raise ValueError("terminal/review ledger cannot contain a new event")
                if transition.previous_transition_hash != previous.transition_hash():
                    raise ValueError("transition previous hash does not match ledger")
                if transition.from_state is not previous.to_state:
                    raise ValueError("transition from_state does not match ledger")
            if not _is_allowed_transition(transition.from_state, transition.to_state):
                raise ValueError("transition is not allowed")
            for ref_index, reference in enumerate(
                (
                    transition.close_intent_id,
                    transition.close_operation_hash,
                    transition.close_receipt_hash,
                )
            ):
                if reference is not None:
                    if ledger_refs[ref_index] is not None and ledger_refs[ref_index] != reference:
                        raise ValueError("close reference is write-once")
                    ledger_refs[ref_index] = reference
        if self.transitions and self.transitions[-1].to_state is not self.state:
            raise ValueError("latest transition state must match position state")
        for reference in (
            self.close_intent_id,
            self.close_operation_hash,
            self.close_receipt_hash,
        ):
            if reference is not None and not reference.strip():
                raise ValueError("close references must be non-empty")
        top_level_refs = (
            self.close_intent_id,
            self.close_operation_hash,
            self.close_receipt_hash,
        )
        if top_level_refs != tuple(ledger_refs):
            raise ValueError("close references must match transition ledger")
        return self

    def transition(
        self,
        *,
        transition_id: str,
        to_state: PositionLifecycleState | str,
        reason: str,
        observed_at: datetime | str,
        evidence_hash: str,
        close_intent_id: str | None = None,
        close_operation_hash: str | None = None,
        close_receipt_hash: str | None = None,
    ) -> "ManagedOptionPosition":
        self = ManagedOptionPosition.model_validate(self.model_dump(mode="python", warnings=False))
        for reference in (close_intent_id, close_operation_hash, close_receipt_hash):
            if reference is not None and not reference.strip():
                raise ValueError("close reference must be non-empty")
        existing = next(
            (item for item in self.transitions if item.transition_id == transition_id), None
        )
        candidate = PositionTransition.create(
            transition_id=transition_id,
            position_id=self.position_id,
            from_state=self.state,
            to_state=to_state,
            reason=reason,
            observed_at=observed_at,
            evidence_hash=evidence_hash,
            previous_transition_hash=(
                self.transitions[-1].transition_hash() if self.transitions else ""
            ),
            position_snapshot_hash=self.position_snapshot_hash,
            close_intent_id=close_intent_id,
            close_operation_hash=close_operation_hash,
            close_receipt_hash=close_receipt_hash,
        )
        if existing is not None:
            existing_index = self.transitions.index(existing)
            expected_previous_hash = (
                self.transitions[existing_index - 1].transition_hash() if existing_index else ""
            )
            replay_values = candidate.model_dump()
            replay_values.update(
                {
                    "from_state": existing.from_state,
                    "previous_transition_hash": expected_previous_hash,
                }
            )
            replay_values.pop("integrity_hash", None)
            replay = PositionTransition.create(**replay_values)
            if existing == replay:
                return self
            raise ValueError("transition identity already exists with different payload")

        if self.state in _TERMINAL_OR_REVIEW:
            raise ValueError("terminal/review position rejects new transition identity")
        for current, proposed in (
            (self.close_intent_id, close_intent_id),
            (self.close_operation_hash, close_operation_hash),
            (self.close_receipt_hash, close_receipt_hash),
        ):
            if current is not None and proposed is not None and current != proposed:
                raise ValueError("close reference is write-once")
        if to_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError("transition is not allowed")
        payload = self.model_dump(mode="python")
        payload.update(
            {
                "state": candidate.to_state,
                "version": self.version + 1,
                "transitions": self.transitions + (candidate,),
                "close_intent_id": close_intent_id or self.close_intent_id,
                "close_operation_hash": close_operation_hash or self.close_operation_hash,
                "close_receipt_hash": close_receipt_hash or self.close_receipt_hash,
            }
        )
        return ManagedOptionPosition.model_validate(payload)

    def _computed_position_snapshot_hash(self) -> str:
        return sha256_hex(
            self.model_dump(
                mode="python",
                include={
                    "position_id",
                    "account_id",
                    "opening_intent_id",
                    "opening_operation_hash",
                    "opening_receipt_hash",
                    "horizon",
                    "direction",
                    "long_leg",
                    "short_leg",
                    "units",
                    "opening_thesis_ref",
                    "thesis_invalidation",
                    "entry_basis_provenance",
                    "exit_policy_version",
                },
            )
        )


_TERMINAL_OR_REVIEW = frozenset(
    {
        PositionLifecycleState.CLOSED_RECONCILED,
        PositionLifecycleState.EXPIRATION_REVIEW,
        PositionLifecycleState.ASSIGNMENT_REVIEW,
        PositionLifecycleState.RECONCILIATION_FAILED,
    }
)
_ALLOWED_TRANSITIONS: dict[PositionLifecycleState, frozenset[PositionLifecycleState]] = {
    PositionLifecycleState.OPEN: frozenset(
        {
            PositionLifecycleState.CLOSE_PENDING,
            PositionLifecycleState.EXPIRATION_REVIEW,
            PositionLifecycleState.ASSIGNMENT_REVIEW,
            PositionLifecycleState.RECONCILIATION_FAILED,
        }
    ),
    PositionLifecycleState.CLOSE_PENDING: frozenset(
        {
            PositionLifecycleState.CLOSE_FILLED,
            PositionLifecycleState.EXPIRATION_REVIEW,
            PositionLifecycleState.ASSIGNMENT_REVIEW,
            PositionLifecycleState.RECONCILIATION_FAILED,
        }
    ),
    PositionLifecycleState.CLOSE_FILLED: frozenset(
        {
            PositionLifecycleState.CLOSED_RECONCILED,
            PositionLifecycleState.EXPIRATION_REVIEW,
            PositionLifecycleState.ASSIGNMENT_REVIEW,
            PositionLifecycleState.RECONCILIATION_FAILED,
        }
    ),
}


def _is_allowed_transition(
    from_state: PositionLifecycleState, to_state: PositionLifecycleState
) -> bool:
    if from_state in _TERMINAL_OR_REVIEW:
        return to_state is from_state
    return to_state in _ALLOWED_TRANSITIONS[from_state]


def _parse_expiration(symbol: str) -> date:
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        raise ValueError("symbols must be valid US OCC option symbols")
    value = match.group("expiry")
    try:
        return date(2000 + int(value[:2]), int(value[2:4]), int(value[4:6]))
    except ValueError as error:
        raise ValueError("symbols must contain a valid OCC expiration") from error


def _parse_contract(symbol: str) -> tuple[date, str, int]:
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        raise ValueError("symbols must be valid US OCC option symbols")
    value = match.group("expiry")
    try:
        expiration = date(2000 + int(value[:2]), int(value[2:4]), int(value[4:6]))
    except ValueError as error:
        raise ValueError("symbols must contain a valid OCC expiration") from error
    return expiration, match.group("option_type"), int(match.group("strike"))


def _underlying(symbol: str) -> str:
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        raise ValueError("symbols must be valid US OCC option symbols")
    return symbol[: match.start("expiry")]
