import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from convictionos.domain.eligibility import EligibilityManifest, evaluate_manifest_outcome
from convictionos.domain.intelligence import IntelligenceArtifact, ThesisDirection
from convictionos.domain.market_data import MarketDataCapability
from convictionos.domain.positions import (
    ManagedOptionPosition,
    PositionLifecycleState,
    PositionTransition,
)
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.trading import IntentState, TradeIntent
from convictionos.infrastructure.models import (
    BrokerActivityCursorRow,
    DecisionReceiptRow,
    EligibilityManifestRow,
    IntelligenceArtifactRow,
    ManagedOptionPositionRow,
    OutboxRow,
    PositionTransitionRow,
    RiskReservationRow,
    TradeIntentRow,
)

if TYPE_CHECKING:
    from convictionos.infrastructure.alpaca_market import PaperPosition


class CapacityExceeded(RuntimeError):
    pass


class ReservationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class SubmissionClaim:
    should_submit: bool
    broker_order_id: str | None
    state: IntentState


_BROKER_STATE_RANK = {
    IntentState.SUBMITTING: 0,
    IntentState.ACK_UNKNOWN: 0,
    IntentState.REPLACEMENT_PENDING: 0,
    IntentState.PARTIALLY_FILLED: 1,
    IntentState.RECONCILED: 2,
}


def validate_broker_transition(
    current_state: IntentState,
    next_state: IntentState,
    existing_broker_order_id: str | None,
    broker_order_id: str,
) -> None:
    if existing_broker_order_id is not None and existing_broker_order_id != broker_order_id:
        raise ValueError("intent was reconciled with a different broker order")
    if current_state is IntentState.RECONCILED:
        if next_state is IntentState.RECONCILED and existing_broker_order_id == broker_order_id:
            return
        raise ValueError("reconciled intent cannot accept another broker transition")
    if next_state not in _BROKER_STATE_RANK:
        raise ValueError("invalid broker-derived intent state")
    if _BROKER_STATE_RANK[next_state] < _BROKER_STATE_RANK[current_state]:
        raise ValueError("cannot regress broker state")


class Store:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _position(row: ManagedOptionPositionRow) -> ManagedOptionPosition:
        position = ManagedOptionPosition.model_validate(row.payload)
        if position.position_id != row.position_id or position.version != row.version:
            raise ValueError("stored position identity or version mismatch")
        if position.position_snapshot_hash != row.position_hash:
            raise ValueError("stored position hash mismatch")
        return position

    async def create_or_load_position(
        self, position: ManagedOptionPosition
    ) -> ManagedOptionPosition:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ManagedOptionPositionRow).where(
                    ManagedOptionPositionRow.opening_intent_id == position.opening_intent_id
                )
            )
            if existing is not None:
                loaded = self._position(existing)
                if loaded.position_snapshot_hash != position.position_snapshot_hash:
                    raise ValueError("opening intent already maps to a different position")
                return loaded
            row = ManagedOptionPositionRow(
                position_id=position.position_id,
                opening_intent_id=position.opening_intent_id,
                account_id=position.account_id,
                underlying=re.split(r"\d", position.long_leg.symbol, maxsplit=1)[0],
                state=position.state.value,
                position_hash=position.position_snapshot_hash,
                payload=position.model_dump(mode="json"),
                version=position.version,
                updated_at=datetime.now(UTC),
            )
            session.add(row)
            return position

    async def get_position(self, position_id: str) -> ManagedOptionPosition:
        async with self._sessions() as session:
            row = await session.get(ManagedOptionPositionRow, position_id)
            if row is None:
                raise KeyError(position_id)
            return self._position(row)

    async def list_managed_positions(
        self, states: tuple[PositionLifecycleState, ...] | None = None
    ) -> list[ManagedOptionPosition]:
        async with self._sessions() as session:
            query = select(ManagedOptionPositionRow)
            if states:
                query = query.where(
                    ManagedOptionPositionRow.state.in_([state.value for state in states])
                )
            rows = (
                await session.scalars(query.order_by(ManagedOptionPositionRow.position_id))
            ).all()
            return [self._position(row) for row in rows]

    async def find_unmanaged_reconciled_openings(
        self,
        account_id: str,
        broker_positions: tuple["PaperPosition", ...],
    ) -> tuple["PaperPosition", ...]:
        managed = await self.list_managed_positions(
            states=(PositionLifecycleState.OPEN, PositionLifecycleState.CLOSE_PENDING)
        )
        remaining = list(broker_positions)
        findings: list[PaperPosition] = []
        for position in managed:
            if position.account_id != account_id:
                continue
            expected = (position.long_leg.symbol, position.short_leg.symbol)
            matches = [item for item in remaining if item.symbol in expected]
            valid = (
                len(matches) == 2
                and {item.symbol for item in matches} == set(expected)
                and all(item.quantity == Decimal(position.units) for item in matches)
            )
            if valid:
                remaining = [item for item in remaining if item not in matches]
            else:
                findings.extend(matches)
        findings.extend(remaining)
        return tuple(findings)

    async def promote_reconciled_openings(
        self,
        account_id: str,
        broker_positions: tuple["PaperPosition", ...],
    ) -> tuple[tuple[ManagedOptionPosition, ...], tuple[str, ...]]:
        """Promote only complete reconciled opening intents into managed positions."""
        promoted: list[ManagedOptionPosition] = []
        findings: list[str] = []
        async with self._sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(TradeIntentRow).where(
                        TradeIntentRow.account_id == account_id,
                        TradeIntentRow.state == IntentState.RECONCILED.value,
                    )
                )
            ).all()
            for intent_row in rows:
                exists = await session.scalar(
                    select(ManagedOptionPositionRow).where(
                        ManagedOptionPositionRow.opening_intent_id == intent_row.intent_id
                    )
                )
                if exists is not None:
                    continue
                intent = TradeIntent.model_validate(intent_row.payload)
                receipt_row = await session.scalar(
                    select(DecisionReceiptRow).where(
                        DecisionReceiptRow.intent_id == intent.intent_id
                    )
                )
                if receipt_row is None:
                    findings.append(f"{intent.intent_id}:missing_receipt")
                    continue
                receipt = DecisionReceipt.model_validate(receipt_row.payload)
                accounting = receipt.paper_accounting
                if accounting is None or len(intent.legs) != 2:
                    findings.append(f"{intent.intent_id}:incomplete_opening_evidence")
                    continue
                symbols = {leg.symbol for leg in intent.legs}
                matches = [item for item in broker_positions if item.symbol in symbols]
                if len(matches) != 2 or {item.symbol for item in matches} != symbols:
                    findings.append(f"{intent.intent_id}:asymmetric_broker_legs")
                    continue
                if any(item.quantity != accounting.broker_reported_filled_qty for item in matches):
                    findings.append(f"{intent.intent_id}:broker_quantity_mismatch")
                    continue
                long_leg = next(
                    (leg for leg in intent.legs if leg.position_intent.value == "buy_to_open"),
                    None,
                )
                short_leg = next(
                    (leg for leg in intent.legs if leg.position_intent.value == "sell_to_open"),
                    None,
                )
                if long_leg is None or short_leg is None:
                    findings.append(f"{intent.intent_id}:invalid_opening_legs")
                    continue
                try:
                    long_strike = int(long_leg.symbol[-8:])
                    short_strike = int(short_leg.symbol[-8:])
                    direction = (
                        ThesisDirection.BULLISH
                        if long_leg.symbol[-9] == "C" and long_strike < short_strike
                        else ThesisDirection.BEARISH
                        if long_leg.symbol[-9] == "P" and long_strike > short_strike
                        else None
                    )
                except (IndexError, ValueError):
                    direction = None
                if direction is None:
                    findings.append(f"{intent.intent_id}:unsupported_opening_shape")
                    continue
                position = ManagedOptionPosition.model_construct(
                    position_id=f"position-{intent.intent_id}",
                    account_id=account_id,
                    opening_intent_id=intent.intent_id,
                    opening_operation_hash=intent.operation_hash(),
                    opening_receipt_hash=receipt.receipt_hash(),
                    horizon=intent.horizon,
                    direction=direction,
                    long_leg=long_leg,
                    short_leg=short_leg,
                    units=int(accounting.broker_reported_filled_qty),
                    opening_thesis_ref=intent.thesis_ref,
                    thesis_invalidation=intent.exit_plan,
                    entry_basis_provenance=accounting,
                    exit_policy_version="exit-policy-v1",
                )
                position = ManagedOptionPosition(
                    **{
                        **position.model_dump(mode="python"),
                        "position_snapshot_hash": position._computed_position_snapshot_hash(),
                    }
                )
                session.add(
                    ManagedOptionPositionRow(
                        position_id=position.position_id,
                        opening_intent_id=position.opening_intent_id,
                        account_id=position.account_id,
                        underlying=re.split(r"\d", long_leg.symbol, maxsplit=1)[0],
                        state=position.state.value,
                        position_hash=position.position_snapshot_hash,
                        payload=position.model_dump(mode="json"),
                        version=position.version,
                        updated_at=datetime.now(UTC),
                    )
                )
                promoted.append(position)
        return tuple(promoted), tuple(findings)

    async def transition_position(
        self,
        position: ManagedOptionPosition,
        transition: PositionTransition,
        expected_version: int,
    ) -> ManagedOptionPosition:
        async with self._sessions() as session, session.begin():
            row = await session.get(
                ManagedOptionPositionRow,
                position.position_id,
                with_for_update=True,
            )
            if row is None:
                raise KeyError(position.position_id)
            current = self._position(row)
            if current.version != expected_version:
                raise ValueError("position version conflict")
            updated = current.transition(
                transition_id=transition.transition_id,
                to_state=transition.to_state,
                reason=transition.reason,
                observed_at=transition.observed_at,
                evidence_hash=transition.evidence_hash,
                close_intent_id=transition.close_intent_id,
                close_operation_hash=transition.close_operation_hash,
                close_receipt_hash=transition.close_receipt_hash,
            )
            if updated == current:
                return current
            row.state = updated.state.value
            row.version = updated.version
            row.payload = updated.model_dump(mode="json")
            row.updated_at = datetime.now(UTC)
            session.add(
                PositionTransitionRow(
                    transition_id=transition.transition_id,
                    position_id=transition.position_id,
                    event_identity=transition.transition_id,
                    previous_transition_hash=transition.previous_transition_hash,
                    transition_hash=transition.transition_hash(),
                    payload=transition.model_dump(mode="json"),
                    observed_at=transition.observed_at,
                )
            )
            return updated

    async def attach_close_intent(
        self,
        position_id: str,
        intent: TradeIntent,
        transition: PositionTransition,
        expected_version: int,
    ) -> ManagedOptionPosition:
        if intent.position_id != position_id:
            raise ValueError("close intent position does not match aggregate")
        if transition.to_state is not PositionLifecycleState.CLOSE_PENDING:
            raise ValueError("close intent must transition position to close_pending")
        async with self._sessions() as session, session.begin():
            row = await session.get(ManagedOptionPositionRow, position_id, with_for_update=True)
            if row is None:
                raise KeyError(position_id)
            current = self._position(row)
            existing_intent = await session.scalar(
                select(TradeIntentRow).where(
                    TradeIntentRow.idempotency_key == intent.idempotency_key
                )
            )
            if existing_intent is not None:
                if existing_intent.operation_hash != intent.operation_hash():
                    raise ValueError("idempotency key already exists with different operation hash")
                if row.close_intent_id not in (None, intent.intent_id):
                    raise ValueError("position already has a different close intent")
                if current.state is PositionLifecycleState.CLOSE_PENDING:
                    return current
            else:
                session.add(
                    TradeIntentRow(
                        intent_id=intent.intent_id,
                        idempotency_key=intent.idempotency_key,
                        account_id=intent.account_id,
                        operation_hash=intent.operation_hash(),
                        state=IntentState.VALIDATED.value,
                        payload=intent.model_dump(mode="json"),
                    )
                )
                await session.flush()
            if current.version != expected_version:
                raise ValueError("position version conflict")
            updated = current.transition(
                transition_id=transition.transition_id,
                to_state=transition.to_state,
                reason=transition.reason,
                observed_at=transition.observed_at,
                evidence_hash=transition.evidence_hash,
                close_intent_id=intent.intent_id,
                close_operation_hash=intent.operation_hash(),
            )
            row.close_intent_id = intent.intent_id
            row.state = updated.state.value
            row.version = updated.version
            row.payload = updated.model_dump(mode="json")
            row.updated_at = datetime.now(UTC)
            session.add(
                PositionTransitionRow(
                    transition_id=transition.transition_id,
                    position_id=position_id,
                    event_identity=transition.transition_id,
                    previous_transition_hash=transition.previous_transition_hash,
                    transition_hash=transition.transition_hash(),
                    payload=transition.model_dump(mode="json"),
                    observed_at=transition.observed_at,
                )
            )
            return updated

    async def claim_open_positions(self, limit: int) -> list[ManagedOptionPosition]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(ManagedOptionPositionRow)
                    .where(ManagedOptionPositionRow.state == PositionLifecycleState.OPEN.value)
                    .order_by(ManagedOptionPositionRow.position_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            return [self._position(row) for row in rows]

    async def get_activity_cursor(self, account_id: str, activity_type: str) -> str | None:
        async with self._sessions() as session:
            row = await session.get(BrokerActivityCursorRow, (account_id, activity_type))
            return row.cursor if row is not None else None

    async def advance_activity_cursor(
        self, account_id: str, activity_type: str, cursor: str
    ) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.get(
                BrokerActivityCursorRow, (account_id, activity_type), with_for_update=True
            )
            if row is None:
                session.add(
                    BrokerActivityCursorRow(
                        account_id=account_id,
                        activity_type=activity_type,
                        cursor=cursor,
                        updated_at=datetime.now(UTC),
                    )
                )
                return
            if row.cursor is not None and cursor <= row.cursor:
                return
            row.cursor = cursor
            row.updated_at = datetime.now(UTC)

    async def create_intent(self, intent: TradeIntent) -> str:
        async with self._sessions() as session:
            session.add(
                TradeIntentRow(
                    intent_id=intent.intent_id,
                    idempotency_key=intent.idempotency_key,
                    account_id=intent.account_id,
                    operation_hash=intent.operation_hash(),
                    state=IntentState.VALIDATED.value,
                    payload=intent.model_dump(mode="json"),
                )
            )
            try:
                await session.commit()
                return intent.intent_id
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(TradeIntentRow).where(
                        TradeIntentRow.idempotency_key == intent.idempotency_key
                    )
                )
                if existing is None:
                    raise
                if existing.operation_hash != intent.operation_hash():
                    raise ValueError(
                        "idempotency key already exists with different operation hash"
                    ) from None
                return existing.intent_id

    async def authorize_with_reservation(
        self,
        intent_id: str,
        account_id: str,
        amount: Decimal,
        account_capacity: Decimal,
    ) -> None:
        if amount <= 0 or account_capacity <= 0:
            raise ValueError("amount and account capacity must be positive")

        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None:
                raise ValueError("intent must exist")
            if row.account_id != account_id:
                raise ReservationConflict("account does not match intent")
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:account_id))"),
                {"account_id": account_id},
            )
            if row.state == IntentState.AUTHORIZED.value:
                existing = await session.scalar(
                    select(RiskReservationRow).where(RiskReservationRow.intent_id == intent_id)
                )
                if (
                    existing is not None
                    and existing.account_id == account_id
                    and existing.amount == amount
                ):
                    return
                raise ReservationConflict("intent is already authorized with different reservation")
            if row.state != IntentState.VALIDATED.value:
                raise ValueError("intent must exist in validated state")
            reserved = await session.scalar(
                select(func.coalesce(func.sum(RiskReservationRow.amount), 0)).where(
                    RiskReservationRow.account_id == account_id,
                    RiskReservationRow.status.in_(("active", "consumed")),
                )
            )
            if Decimal(str(reserved)) + amount > account_capacity:
                raise CapacityExceeded(account_id)
            operation_hash = row.operation_hash
            session.add(
                RiskReservationRow(
                    intent_id=intent_id,
                    account_id=account_id,
                    amount=amount,
                    status="active",
                )
            )
            row.state = IntentState.AUTHORIZED.value
            session.add(
                OutboxRow(
                    aggregate_id=intent_id,
                    topic="trade_intent.authorized",
                    payload={"intent_id": intent_id, "operation_hash": operation_hash},
                    created_at=datetime.now(UTC),
                )
            )

    async def get_state(self, intent_id: str) -> IntentState:
        async with self._sessions() as session:
            value = await session.scalar(
                select(TradeIntentRow.state).where(TradeIntentRow.intent_id == intent_id)
            )
            if value is None:
                raise KeyError(intent_id)
            return IntentState(value)

    async def find_reservation(self, intent_id: str) -> RiskReservationRow | None:
        async with self._sessions() as session:
            return cast(
                RiskReservationRow | None,
                await session.scalar(
                    select(RiskReservationRow).where(RiskReservationRow.intent_id == intent_id)
                ),
            )

    async def claim_submission(self, intent_id: str) -> SubmissionClaim:
        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None:
                raise KeyError(intent_id)
            state = IntentState(row.state)
            if state is IntentState.RECONCILED:
                return SubmissionClaim(False, row.broker_order_id, state)
            if state is IntentState.AUTHORIZED:
                row.state = IntentState.SUBMITTING.value
                return SubmissionClaim(True, row.broker_order_id, IntentState.SUBMITTING)
            if state in {
                IntentState.SUBMITTING,
                IntentState.ACK_UNKNOWN,
                IntentState.REPLACEMENT_PENDING,
                IntentState.PARTIALLY_FILLED,
            }:
                return SubmissionClaim(False, row.broker_order_id, state)
            if state in {IntentState.REJECTED, IntentState.CANCELED}:
                return SubmissionClaim(False, row.broker_order_id, state)
            raise ValueError(f"cannot submit intent in state {row.state}")

    async def load_intent(self, intent_id: str) -> TradeIntent:
        async with self._sessions() as session:
            row = await session.get(TradeIntentRow, intent_id)
            if row is None:
                raise KeyError(intent_id)
            intent = TradeIntent.model_validate(row.payload)
            if intent.operation_hash() != row.operation_hash:
                raise ValueError("stored operation hash mismatch")
            return intent

    async def record_broker_order(
        self, intent_id: str, broker_order_id: str, state: IntentState
    ) -> None:
        if state not in {
            IntentState.SUBMITTING,
            IntentState.PARTIALLY_FILLED,
            IntentState.RECONCILED,
        }:
            raise ValueError("invalid broker-derived intent state")
        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None:
                raise ValueError("intent is not awaiting broker reconciliation")
            if row.state == IntentState.RECONCILED.value:
                validate_broker_transition(
                    IntentState.RECONCILED,
                    state,
                    row.broker_order_id,
                    broker_order_id,
                )
                return
            if row.state not in {
                IntentState.SUBMITTING.value,
                IntentState.ACK_UNKNOWN.value,
                IntentState.REPLACEMENT_PENDING.value,
                IntentState.PARTIALLY_FILLED.value,
            }:
                raise ValueError("intent is not awaiting broker reconciliation")
            current_state = IntentState(row.state)
            if current_state is IntentState.REPLACEMENT_PENDING:
                row.broker_order_id = broker_order_id
                # Alpaca can expose the replacement as new or partially filled
                # before its terminal state is known.  Keep the explicit hold
                # until that terminal observation so a later retry can never
                # submit the original idempotency key again.
                if state in {IntentState.SUBMITTING, IntentState.PARTIALLY_FILLED}:
                    return
                row.state = state.value
                if state is IntentState.RECONCILED:
                    reservation = await session.scalar(
                        select(RiskReservationRow).where(RiskReservationRow.intent_id == intent_id)
                    )
                    if reservation is not None:
                        reservation.status = "consumed"
                return
            validate_broker_transition(current_state, state, row.broker_order_id, broker_order_id)
            row.broker_order_id = broker_order_id
            row.state = state.value
            if state is IntentState.RECONCILED:
                reservation = await session.scalar(
                    select(RiskReservationRow).where(RiskReservationRow.intent_id == intent_id)
                )
                if reservation is not None:
                    reservation.status = "consumed"

    async def mark_replacement_pending(self, intent_id: str, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None or row.state not in {
                IntentState.SUBMITTING.value,
                IntentState.REPLACEMENT_PENDING.value,
                IntentState.PARTIALLY_FILLED.value,
            }:
                raise ValueError("intent is not awaiting replacement reconciliation")
            row.state = IntentState.REPLACEMENT_PENDING.value
            session.add(
                OutboxRow(
                    aggregate_id=intent_id,
                    topic="trade_intent.replacement_pending",
                    payload={"intent_id": intent_id, "reason": reason},
                    created_at=datetime.now(UTC),
                )
            )

    async def mark_ack_unknown(self, intent_id: str, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None or row.state not in {
                IntentState.SUBMITTING.value,
                IntentState.ACK_UNKNOWN.value,
            }:
                raise ValueError("intent is not awaiting unknown-ack reconciliation")
            row.state = IntentState.ACK_UNKNOWN.value
            session.add(
                OutboxRow(
                    aggregate_id=intent_id,
                    topic="trade_intent.ack_unknown",
                    payload={"intent_id": intent_id, "reason": reason},
                    created_at=datetime.now(UTC),
                )
            )

    async def mark_not_filled(
        self,
        intent_id: str,
        state: IntentState,
        reason: str,
        *,
        broker_order_id: str | None = None,
    ) -> None:
        if state not in {IntentState.REJECTED, IntentState.CANCELED}:
            raise ValueError("terminal state must be rejected or canceled")
        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None or row.state not in {
                IntentState.SUBMITTING.value,
                IntentState.ACK_UNKNOWN.value,
                IntentState.REPLACEMENT_PENDING.value,
                IntentState.PARTIALLY_FILLED.value,
            }:
                raise ValueError("intent is not terminally rejectable")
            if row.state == IntentState.PARTIALLY_FILLED.value:
                raise ValueError(
                    "cannot terminally reject a partially filled intent without "
                    "partial reservation accounting"
                )
            if broker_order_id is not None:
                if (
                    row.broker_order_id is not None
                    and row.broker_order_id != broker_order_id
                    and row.state != IntentState.REPLACEMENT_PENDING.value
                ):
                    raise ValueError("intent was reconciled with a different broker order")
                row.broker_order_id = broker_order_id
            row.state = state.value
            reservation = await session.scalar(
                select(RiskReservationRow).where(RiskReservationRow.intent_id == intent_id)
            )
            if reservation is not None:
                reservation.status = "released"
            session.add(
                OutboxRow(
                    aggregate_id=intent_id,
                    topic=f"trade_intent.{state.value}",
                    payload={"intent_id": intent_id, "reason": reason},
                    created_at=datetime.now(UTC),
                )
            )

    async def recover_submission(self, intent_id: str, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            row = await session.get(TradeIntentRow, intent_id, with_for_update=True)
            if row is None:
                raise KeyError(intent_id)
            if row.state == IntentState.SUBMITTING.value:
                row.state = IntentState.AUTHORIZED.value
                session.add(
                    OutboxRow(
                        aggregate_id=intent_id,
                        topic="trade_intent.submission_reset",
                        payload={"intent_id": intent_id, "reason": reason},
                        created_at=datetime.now(UTC),
                    )
                )
                return
            if row.state == IntentState.RECONCILED.value:
                return
            raise ValueError(f"cannot reset submission in state {row.state}")

    async def reset_submission(self, intent_id: str, reason: str) -> None:
        await self.recover_submission(intent_id, reason)

    async def save_receipt(self, receipt: DecisionReceipt) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                DecisionReceiptRow(
                    receipt_id=receipt.receipt_id,
                    intent_id=receipt.intent_id,
                    previous_receipt_hash=receipt.previous_receipt_hash,
                    receipt_hash=receipt.receipt_hash(),
                    payload=receipt.model_dump(mode="json"),
                )
            )

    async def get_receipt(self, intent_id: str) -> DecisionReceipt:
        receipt = await self.find_receipt(intent_id)
        if receipt is None:
            raise KeyError(intent_id)
        return receipt

    async def find_receipt(self, intent_id: str) -> DecisionReceipt | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(DecisionReceiptRow).where(DecisionReceiptRow.intent_id == intent_id)
            )
            if row is None:
                return None
            receipt = DecisionReceipt.model_validate(row.payload)
            if receipt.receipt_hash() != row.receipt_hash:
                raise ValueError("receipt hash mismatch")
            return receipt

    async def find_intelligence_artifact(self, identity_hash: str) -> IntelligenceArtifact | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(IntelligenceArtifactRow).where(
                    IntelligenceArtifactRow.identity_hash == identity_hash
                )
            )
            if row is None:
                return None
            return _load_intelligence_artifact(row)

    async def create_or_load_intelligence_artifact(
        self, artifact: IntelligenceArtifact
    ) -> IntelligenceArtifact:
        artifact_hash = artifact.artifact_hash()
        async with self._sessions() as session:
            existing = await session.scalar(
                select(IntelligenceArtifactRow).where(
                    IntelligenceArtifactRow.identity_hash == artifact.identity_hash
                )
            )
            if existing is not None:
                return _check_intelligence_artifact(existing, artifact_hash)

            session.add(
                IntelligenceArtifactRow(
                    artifact_id=artifact.artifact_id,
                    identity_hash=artifact.identity_hash,
                    artifact_hash=artifact_hash,
                    evidence_snapshot_hash=artifact.evidence_snapshot_hash,
                    provider=artifact.provider,
                    model=artifact.model,
                    payload=artifact.model_dump(mode="json"),
                    created_at=artifact.generated_at,
                )
            )
            try:
                await session.commit()
                return artifact
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(IntelligenceArtifactRow).where(
                        IntelligenceArtifactRow.identity_hash == artifact.identity_hash
                    )
                )
                if existing is None:
                    raise
                return _check_intelligence_artifact(existing, artifact_hash)

    async def create_or_load_eligibility_manifest(
        self, manifest: EligibilityManifest
    ) -> EligibilityManifest:
        manifest_hash = manifest.manifest_hash()
        async with self._sessions() as session:
            existing = await session.get(EligibilityManifestRow, manifest.manifest_id)
            if existing is not None:
                return _check_eligibility_manifest(existing, manifest_hash)

            session.add(
                EligibilityManifestRow(
                    manifest_id=manifest.manifest_id,
                    manifest_hash=manifest_hash,
                    workspace_id=manifest.workspace_id,
                    account_id=manifest.observed_account_id,
                    outcome=manifest.outcome.value,
                    verified_at=manifest.verified_at,
                    payload=manifest.model_dump(mode="json"),
                    baseline_manifest_id=manifest.baseline_manifest_id,
                )
            )
            try:
                await session.commit()
                return manifest
            except IntegrityError:
                await session.rollback()
                existing = await session.get(EligibilityManifestRow, manifest.manifest_id)
                if existing is None:
                    raise
                return _check_eligibility_manifest(existing, manifest_hash)

    async def latest_eligibility_manifest(
        self, workspace_id: str, account_id: str
    ) -> EligibilityManifest | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(EligibilityManifestRow)
                .where(
                    EligibilityManifestRow.workspace_id == workspace_id,
                    EligibilityManifestRow.account_id == account_id,
                )
                .order_by(EligibilityManifestRow.verified_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            return _load_eligibility_manifest(row)

    async def latest_baseline(
        self, workspace_id: str, account_id: str
    ) -> EligibilityManifest | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(EligibilityManifestRow)
                .where(
                    EligibilityManifestRow.workspace_id == workspace_id,
                    EligibilityManifestRow.account_id == account_id,
                    EligibilityManifestRow.baseline_manifest_id.is_(None),
                )
                .order_by(EligibilityManifestRow.verified_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            return _load_eligibility_manifest(row)


def _load_intelligence_artifact(row: IntelligenceArtifactRow) -> IntelligenceArtifact:
    artifact = IntelligenceArtifact.model_validate(row.payload)
    if artifact.artifact_hash() != row.artifact_hash:
        raise ValueError("stored artifact hash mismatch")
    if artifact.identity_hash != row.identity_hash:
        raise ValueError("stored artifact identity hash mismatch")
    return artifact


def _check_intelligence_artifact(
    row: IntelligenceArtifactRow, requested_hash: str
) -> IntelligenceArtifact:
    if row.artifact_hash != requested_hash:
        raise ValueError("identity already exists with different artifact hash")
    return _load_intelligence_artifact(row)


def _load_eligibility_manifest(row: EligibilityManifestRow) -> EligibilityManifest:
    try:
        manifest = EligibilityManifest.model_validate(row.payload)
    except ValidationError:
        manifest = _load_legacy_eligibility_manifest(row.payload)
    else:
        if manifest.manifest_hash() != row.manifest_hash:
            raise ValueError("stored manifest hash mismatch")
    if manifest.manifest_id != row.manifest_id:
        raise ValueError("stored manifest identity mismatch")
    return manifest


def _load_legacy_eligibility_manifest(
    payload: dict[str, object]
) -> EligibilityManifest:
    _assert_legacy_indicative_opra_gate(payload)
    normalized = deepcopy(payload)
    capability = MarketDataCapability.model_validate(
        normalized["market_data_capability"]
    )
    outcome, checks = evaluate_manifest_outcome(
        expected_account_id=str(normalized["expected_account_id"]),
        observed_account_id=str(normalized["observed_account_id"]),
        environment=str(normalized["environment"]),
        account_status=str(normalized["account_status"]),
        starting_equity=Decimal(str(normalized["starting_equity"])),
        starting_cash=Decimal(str(normalized["starting_cash"])),
        no_positions_at_baseline=bool(normalized["no_positions_at_baseline"]),
        no_orders_at_baseline=bool(normalized["no_orders_at_baseline"]),
        options_level=int(normalized["options_level"]),
        trading_api_read_ok=bool(normalized["trading_api_read_ok"]),
        cli_revision=str(normalized["cli_revision"]),
        cli_digest=str(normalized["cli_digest"]),
        mcp_schema_hash=str(normalized["mcp_schema_hash"]),
        market_data_capability=capability,
        mandate_version=str(normalized["mandate_version"]),
        baseline_manifest_id=(
            str(normalized["baseline_manifest_id"])
            if normalized.get("baseline_manifest_id") is not None
            else None
        ),
    )
    normalized["outcome"] = outcome.value
    normalized["check_results"] = tuple(
        check.model_dump(mode="json") for check in checks
    )
    return EligibilityManifest.model_validate(normalized)


def _assert_legacy_indicative_opra_gate(payload: dict[str, object]) -> None:
    if payload.get("outcome") != "ineligible" or payload.get("option_feed") != "indicative":
        raise ValueError("stored manifest cannot be normalized")
    check_results = payload.get("check_results")
    if not isinstance(check_results, list):
        raise ValueError("stored manifest cannot be normalized")
    if not any(
        isinstance(check, dict)
        and check.get("name") == "performance_feed"
        and check.get("status") == "fail"
        and check.get("public_reason") == "OPRA feed is required for performance evidence"
        for check in check_results
    ):
        raise ValueError("stored manifest cannot be normalized")


def _check_eligibility_manifest(
    row: EligibilityManifestRow, requested_hash: str
) -> EligibilityManifest:
    if row.manifest_hash != requested_hash:
        raise ValueError("manifest id already exists with different manifest hash")
    return _load_eligibility_manifest(row)
