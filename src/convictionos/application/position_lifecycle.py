from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from convictionos.application.close_intents import CloseIntentFactory, authorize_close
from convictionos.application.exit_policy import ExitDecision, ExitOutcome
from convictionos.domain.canonical import sha256_hex
from convictionos.domain.positions import (
    ManagedOptionPosition,
    PositionLifecycleState,
    PositionTransition,
)
from convictionos.domain.trading import TradeIntent
from convictionos.infrastructure.alpaca_activities import BrokerActivityType
from convictionos.infrastructure.alpaca_market import PaperPosition
from convictionos.infrastructure.brokers import BrokerOrderStatus


class LifecyclePositionPort(Protocol):
    async def promote_reconciled_openings(
        self, account_id: str, broker_positions: tuple[PaperPosition, ...]
    ) -> tuple[tuple[ManagedOptionPosition, ...], tuple[str, ...]]: ...

    async def list_managed_positions(
        self, states: tuple[PositionLifecycleState, ...] | None = None
    ) -> list[ManagedOptionPosition]: ...

    async def find_unmanaged_reconciled_openings(
        self, account_id: str, broker_positions: tuple[PaperPosition, ...]
    ) -> tuple[PaperPosition, ...]: ...

    async def attach_close_intent(
        self,
        position_id: str,
        intent: TradeIntent,
        transition: PositionTransition,
        expected_version: int,
    ) -> ManagedOptionPosition: ...

    async def transition_position(
        self,
        position: ManagedOptionPosition,
        transition: PositionTransition,
        expected_version: int,
    ) -> ManagedOptionPosition: ...


class BrokerPositionPort(Protocol):
    async def get_positions(self) -> tuple[Any, ...]: ...

    async def get_account(self) -> BrokerAccountPort: ...


class BrokerAccountPort(Protocol):
    account_id: str


class BrokerActivityPort(Protocol):
    async def list_activities(
        self,
        activity_type: BrokerActivityType,
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        page_token: str | None = None,
    ) -> tuple[tuple[Any, ...], str | None]: ...


class ExitDecisionPort(Protocol):
    async def evaluate(
        self, position: ManagedOptionPosition, *, now: datetime
    ) -> ExitDecision: ...


class CloseExecutionPort(Protocol):
    async def execute(self, intent_id: str) -> object: ...


def map_close_order_state(
    status: BrokerOrderStatus,
    *,
    complete_spread_present: bool,
) -> PositionLifecycleState:
    """Map only known broker states; unknown states remain pending."""
    if status in {BrokerOrderStatus.NEW, BrokerOrderStatus.PARTIALLY_FILLED}:
        return PositionLifecycleState.CLOSE_PENDING
    if status is BrokerOrderStatus.FILLED:
        return PositionLifecycleState.CLOSE_FILLED
    if status in {BrokerOrderStatus.REJECTED, BrokerOrderStatus.CANCELED}:
        if complete_spread_present:
            return PositionLifecycleState.OPEN
        return PositionLifecycleState.RECONCILIATION_FAILED
    return PositionLifecycleState.CLOSE_PENDING


def map_broker_activity_state(
    activity_type: BrokerActivityType,
) -> PositionLifecycleState | None:
    if activity_type is BrokerActivityType.OPASN:
        return PositionLifecycleState.ASSIGNMENT_REVIEW
    if activity_type in {BrokerActivityType.OPEXP, BrokerActivityType.OPXRC}:
        return PositionLifecycleState.EXPIRATION_REVIEW
    return None


class LifecycleCycleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: datetime
    open_count: int = Field(ge=0)
    close_pending_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    transitioned_public_ids: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    close_submissions: int = Field(default=0, ge=0)
    block_new_entries: bool


class PositionLifecycleService:
    """Read/reconcile broker exposure before entry; never invents lifecycle evidence."""

    def __init__(
        self,
        store: LifecyclePositionPort,
        broker_positions: BrokerPositionPort,
        account_id: str | None,
        exit_decisions: ExitDecisionPort | None = None,
        close_execution: CloseExecutionPort | None = None,
        activities: BrokerActivityPort | None = None,
        orders: Any | None = None,
    ) -> None:
        self._store = store
        self._broker_positions = broker_positions
        self._account_id = account_id
        self._exit_decisions = exit_decisions
        self._close_execution = close_execution
        self._activities = activities
        self._orders = orders

    async def _reconcile_activities(
        self,
        positions: list[ManagedOptionPosition],
        *,
        account_id: str,
        now: datetime,
    ) -> tuple[list[str], tuple[str, ...]]:
        if self._activities is None:
            return [], ()
        get_cursor = getattr(self._store, "get_activity_cursor", None)
        advance_cursor = getattr(self._store, "advance_activity_cursor", None)
        if get_cursor is None or advance_cursor is None:
            return [], ("broker_activity_cursor_unavailable",)
        transitioned: list[str] = []
        reasons: list[str] = []
        by_symbol = {
            symbol: position
            for position in positions
            for symbol in (position.long_leg.symbol, position.short_leg.symbol)
        }
        for activity_type in BrokerActivityType:
            page_token = await get_cursor(account_id, activity_type.value)
            last_activity_id = page_token
            try:
                while True:
                    page, next_token = await self._activities.list_activities(
                        activity_type, until=now, page_token=page_token
                    )
                    for activity in page:
                        last_activity_id = activity.activity_id
                        symbol = getattr(activity, "symbol", None)
                        target = by_symbol.get(symbol) if isinstance(symbol, str) else None
                        target_state = map_broker_activity_state(activity_type)
                        if target is not None and target_state is not None and target.state in {
                            PositionLifecycleState.OPEN,
                            PositionLifecycleState.CLOSE_PENDING,
                        }:
                            transition = PositionTransition.create(
                                transition_id=f"{target.position_id}-{activity.activity_id}",
                                position_id=target.position_id,
                                from_state=target.state,
                                to_state=target_state,
                                reason=f"broker activity {activity_type.value}",
                                observed_at=now,
                                evidence_hash=activity.content_hash,
                                position_snapshot_hash=target.position_snapshot_hash,
                            )
                            await self._store.transition_position(
                                target, transition, target.version
                            )
                            transitioned.append(target.position_id)
                    if next_token is None:
                        break
                    page_token = next_token
                if last_activity_id is not None:
                    await advance_cursor(account_id, activity_type.value, last_activity_id)
            except Exception:
                reasons.append(f"{activity_type.value.lower()}_reconciliation_failed")
        return transitioned, tuple(reasons)

    async def run_cycle(
        self, *, now: datetime, allow_close_submission: bool
    ) -> LifecycleCycleResult:
        managed = await self._store.list_managed_positions()
        broker = await self._broker_positions.get_positions()
        account_id = self._account_id
        if account_id is None:
            account = await self._broker_positions.get_account()
            account_id = account.account_id
        promoted: tuple[ManagedOptionPosition, ...] = ()
        promotion_findings: tuple[str, ...] = ()
        promote = getattr(self._store, "promote_reconciled_openings", None)
        if promote is not None:
            promoted, promotion_findings = await promote(account_id, broker)
            if promoted:
                managed = [*managed, *promoted]
        findings = await self._store.find_unmanaged_reconciled_openings(
            account_id, broker
        )
        review_reasons: tuple[str, ...] = (
            ("broker_position_mismatch",) if findings else ()
        )
        if promotion_findings:
            review_reasons += ("opening_promotion_failed",)
        activity_transitions, activity_findings = await self._reconcile_activities(
            managed, account_id=account_id, now=now
        )
        transitioned: list[str] = list(activity_transitions)
        review_reasons += activity_findings
        broker_symbols = {item.symbol for item in broker}
        if self._orders is not None:
            for position in managed:
                if (
                    position.state is not PositionLifecycleState.CLOSE_PENDING
                    or position.close_intent_id is None
                ):
                    continue
                try:
                    order = await self._orders.get_by_client_order_id(
                        position.close_intent_id
                    )
                except Exception:
                    review_reasons += ("close_order_lookup_failed",)
                    continue
                if order is None:
                    review_reasons += ("close_order_not_found",)
                    continue
                next_state = map_close_order_state(
                    order.status,
                    complete_spread_present=broker_symbols.issuperset(
                        {position.long_leg.symbol, position.short_leg.symbol}
                    ),
                )
                if next_state is PositionLifecycleState.CLOSE_PENDING:
                    continue
                transition = PositionTransition.create(
                    transition_id=f"{position.position_id}-{order.order_id}-{order.status.value}",
                    position_id=position.position_id,
                    from_state=position.state,
                    to_state=next_state,
                    reason=f"broker close order {order.status.value}",
                    observed_at=now,
                    evidence_hash=sha256_hex(order.model_dump(mode="python")),
                    position_snapshot_hash=position.position_snapshot_hash,
                    close_intent_id=position.close_intent_id,
                    close_operation_hash=position.close_operation_hash,
                )
                await self._store.transition_position(
                    position, transition, position.version
                )
                transitioned.append(position.position_id)
        review_count = sum(
            position.state
            in {
                PositionLifecycleState.ASSIGNMENT_REVIEW,
                PositionLifecycleState.EXPIRATION_REVIEW,
                PositionLifecycleState.RECONCILIATION_FAILED,
            }
            for position in managed
        )
        review_count += len(activity_transitions)
        for position in managed:
            if position.state is not PositionLifecycleState.CLOSE_FILLED:
                continue
            if broker_symbols.intersection(
                {position.long_leg.symbol, position.short_leg.symbol}
            ):
                continue
            transition = PositionTransition.create(
                transition_id=f"{position.position_id}-closed-reconciled-v1",
                position_id=position.position_id,
                from_state=position.state,
                to_state=PositionLifecycleState.CLOSED_RECONCILED,
                reason="both option legs absent at broker",
                observed_at=now,
                evidence_hash=sha256_hex(
                    {"account_id": account_id, "broker_symbols": sorted(broker_symbols)}
                ),
                position_snapshot_hash=position.position_snapshot_hash,
            )
            await self._store.transition_position(position, transition, position.version)
            transitioned.append(position.position_id)
        close_submissions = 0
        if not findings and allow_close_submission and self._exit_decisions is not None:
            for position in managed:
                if position.state is not PositionLifecycleState.OPEN:
                    continue
                decision = await self._exit_decisions.evaluate(position, now=now)
                if decision.outcome is not ExitOutcome.CLOSE:
                    continue
                intent = CloseIntentFactory.create(position, decision, now)
                authorization = authorize_close(position, intent, decision)
                if not authorization.authorized:
                    review_reasons += ("close_authorization_denied",)
                    continue
                transition = PositionTransition.create(
                    transition_id=f"{position.position_id}-close-pending-v1",
                    position_id=position.position_id,
                    from_state=position.state,
                    to_state=PositionLifecycleState.CLOSE_PENDING,
                    reason=decision.primary_reason,
                    observed_at=now,
                    evidence_hash=decision.decision_hash,
                    position_snapshot_hash=position.position_snapshot_hash,
                    close_intent_id=intent.intent_id,
                    close_operation_hash=intent.operation_hash(),
                )
                await self._store.attach_close_intent(
                    position.position_id, intent, transition, position.version
                )
                transitioned.append(position.position_id)
                if self._close_execution is not None:
                    await self._close_execution.execute(intent.intent_id)
                    close_submissions += 1
        return LifecycleCycleResult(
            observed_at=now,
            open_count=sum(position.state is PositionLifecycleState.OPEN for position in managed),
            close_pending_count=sum(
                position.state is PositionLifecycleState.CLOSE_PENDING for position in managed
            ),
            review_count=review_count + bool(findings),
            transitioned_public_ids=tuple(transitioned),
            review_reasons=review_reasons,
            close_submissions=close_submissions,
            block_new_entries=(
                bool(findings)
                or review_count > 0
                or bool(review_reasons)
                or bool(activity_transitions)
            ),
        )
