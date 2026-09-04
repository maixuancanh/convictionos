from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

import httpx
from pydantic import BaseModel, ConfigDict, Field

from convictionos.application.execution import ExecutionOrchestrator
from convictionos.application.grounded_intelligence import GroundedIntelligenceService
from convictionos.application.market_data_policy import evaluate_market_data_policy
from convictionos.application.market_truth import (
    MarketTruthObservationPort,
    MarketTruthOutcome,
    MarketTruthPacket,
    MarketTruthService,
)
from convictionos.application.portfolio_risk import (
    PortfolioRiskService,
    RiskDecision,
)
from convictionos.domain.canonical import sha256_hex
from convictionos.domain.intelligence import (
    EvidenceBundle,
    EvidenceItem,
    GroundedThesis,
    ThesisDirection,
)
from convictionos.domain.mandates import (
    ApprovalMode,
    KillSwitch,
    Mandate,
    PolicyContext,
    PolicyOutcome,
    evaluate_policy,
)
from convictionos.domain.market_data import MarketDataCapability
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.portfolio_risk import HorizonRiskBudget, PortfolioSnapshot
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.strategy import OptionMarketQuote, StrategyCandidate
from convictionos.domain.trading import (
    Horizon,
    InstrumentKind,
    IntentState,
    OrderType,
    PositionIntent,
    Side,
    TradeIntent,
    TradeLeg,
)
from convictionos.infrastructure.brokers import BrokerOrder, BrokerOrderStatus, BrokerPort
from convictionos.infrastructure.store import Store

if TYPE_CHECKING:
    from convictionos.application.strategy_router import QuantSignal

_POSITION_SYMBOL = re.compile(r"^([A-Za-z.]+)\d{6}[CP]\d{8}$")
_PIPELINE_FRESHNESS = timedelta(minutes=5)
_PROMPT_VERSION = "grounded-thesis-v1"


class AgentAction(StrEnum):
    TRADE = "trade"
    ABSTAIN = "abstain"


class OptionQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    strike: Decimal = Field(gt=0)
    expiration: date
    bid_price: Decimal = Field(ge=0)
    ask_price: Decimal = Field(gt=0)
    as_of: datetime

    @property
    def is_usable(self) -> bool:
        return self.bid_price > 0 and self.ask_price >= self.bid_price


class AgentMarketFrame(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: str = Field(min_length=1)
    price: Decimal = Field(gt=0)
    previous_close: Decimal = Field(gt=0)
    observed_at: datetime
    source: str = Field(min_length=1)
    market_data: MarketDataCapability
    calls: tuple[OptionQuote, ...]
    option_quotes: tuple[OptionMarketQuote, ...] = ()
    quote_rejections: tuple[str, ...] = ()

    def snapshot_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))


class AgentDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    action: AgentAction
    underlying: str
    thesis: str
    invalidation: str
    reasons: tuple[str, ...]
    snapshot_hash: str
    long_symbol: str | None = None
    short_symbol: str | None = None
    limit_price: Decimal | None = None
    max_loss: Decimal | None = None
    horizon: Horizon | None = None


class QuantSignalSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: ThesisDirection
    momentum: Decimal
    realized_volatility: Decimal
    as_of: datetime
    version: str


class _RouterRunContext(TypedDict):
    selected_candidate: StrategyCandidate
    alternatives: tuple[StrategyCandidate, ...]
    quant_snapshot: QuantSignalSnapshot
    risk_decision: RiskDecision


class DefinedRiskCallSpreadStrategy:
    """Deterministic, explainable first strategy for the autonomous paper agent."""

    def __init__(
        self,
        *,
        max_age: timedelta = timedelta(minutes=5),
        minimum_momentum: Decimal = Decimal("0.002"),
        max_debit: Decimal = Decimal("2.50"),
    ) -> None:
        self._max_age = max_age
        self._minimum_momentum = minimum_momentum
        self._max_debit = max_debit

    def decide(self, frame: AgentMarketFrame, *, now: datetime) -> AgentDecision:
        snapshot_hash = frame.snapshot_hash()

        def abstain(reason: str) -> AgentDecision:
            return AgentDecision(
                decision_id=sha256_hex(
                    {"strategy": "defined-risk-call-spread-v1", "snapshot": snapshot_hash}
                ),
                action=AgentAction.ABSTAIN,
                underlying=frame.underlying,
                thesis="No trade: safety or signal gate was not satisfied.",
                invalidation="Re-evaluate on the next fresh market observation.",
                reasons=(reason,),
                snapshot_hash=snapshot_hash,
            )

        if now < frame.observed_at or now - frame.observed_at > self._max_age:
            return abstain("market observation is stale")

        momentum = (frame.price - frame.previous_close) / frame.previous_close
        if momentum < self._minimum_momentum:
            return abstain("bullish momentum threshold was not met")

        usable = tuple(
            quote
            for quote in frame.calls
            if quote.is_usable
            and quote.expiration >= now.date()
            and now >= quote.as_of
            and now - quote.as_of <= self._max_age
        )
        if len(usable) < 2:
            return abstain("fewer than two usable call contracts")

        long = min(usable, key=lambda quote: (abs(quote.strike - frame.price), quote.strike))
        short_candidates = tuple(
            quote
            for quote in usable
            if quote.expiration == long.expiration and quote.strike > long.strike
        )
        if not short_candidates:
            return abstain("no higher-strike call is available for defined risk")
        short = min(short_candidates, key=lambda quote: quote.strike)

        debit = (long.ask_price - short.bid_price).quantize(Decimal("0.01"))
        if debit <= 0 or debit > self._max_debit:
            return abstain("spread debit is outside the autonomous risk budget")

        max_loss = (debit * Decimal("100")).quantize(Decimal("0.01"))
        return AgentDecision(
            decision_id=sha256_hex(
                {"strategy": "defined-risk-call-spread-v1", "snapshot": snapshot_hash}
            ),
            action=AgentAction.TRADE,
            underlying=frame.underlying,
            thesis=(
                f"{frame.underlying} trades above its previous close; express the signal "
                "with a capped-loss call debit spread."
            ),
            invalidation="Exit if momentum reverses or before expiration.",
            reasons=("fresh bullish momentum and a valid capped-loss spread",),
            snapshot_hash=snapshot_hash,
            long_symbol=long.symbol,
            short_symbol=short.symbol,
            limit_price=debit,
            max_loss=max_loss,
        )


class AgentRunState(StrEnum):
    ABSTAINED = "abstained"
    DENIED = "denied"
    SUBMITTED = "submitted"
    FILLED = "filled"
    REJECTED = "rejected"


class AgentRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: AgentRunState
    decision: AgentDecision
    intent_id: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    receipt: DecisionReceipt | None = None
    selected_candidate: StrategyCandidate | None = None
    alternatives: tuple[StrategyCandidate, ...] = ()
    quant_snapshot: QuantSignalSnapshot | None = None
    risk_decision: RiskDecision | None = None
    market_data: MarketDataCapability | None = None
    paper_accounting: PaperExecutionAccounting | None = None

    @property
    def quant_signal(self) -> QuantSignalSnapshot | None:
        return self.quant_snapshot


class PaperAccount(Protocol):
    account_id: str
    status: str
    buying_power: Decimal
    options_trading_level: int


class OpenPosition(Protocol):
    symbol: str
    quantity: Decimal


class AgentMarketPort(Protocol):
    async def observe(
        self,
        underlying: str,
        *,
        expiration_gte: date,
        expiration_lte: date,
    ) -> AgentMarketFrame: ...

    async def get_account(self) -> PaperAccount: ...

    async def get_positions(self) -> tuple[OpenPosition, ...]: ...


class EvidencePort(Protocol):
    async def news(
        self, underlying: str, *, observed_at: datetime, lookback_hours: int
    ) -> tuple[EvidenceItem, ...]: ...

    @staticmethod
    def build_bundle(
        frame: AgentMarketFrame, news: tuple[EvidenceItem, ...]
    ) -> EvidenceBundle: ...


class RoutingPort(Protocol):
    def route(
        self, thesis: GroundedThesis, frame: AgentMarketFrame, quant: QuantSignal
    ) -> RoutingDecision: ...


class RoutingDecision(Protocol):
    selected: StrategyCandidate | None
    alternatives: tuple[StrategyCandidate, ...]
    reasons: tuple[str, ...]


class RiskPort(Protocol):
    def evaluate(
        self,
        candidate: StrategyCandidate,
        thesis: GroundedThesis,
        snapshot: PortfolioSnapshot,
    ) -> RiskDecision: ...


class AutonomousTradingAgent:
    """One idempotent autonomous decision-and-execution cycle."""

    def __init__(
        self,
        *,
        store: Store,
        broker: BrokerPort,
        market: AgentMarketPort,
        strategy: DefinedRiskCallSpreadStrategy,
        underlying: str,
        evidence: EvidencePort | None = None,
        intelligence: GroundedIntelligenceService | None = None,
        router: RoutingPort | None = None,
        portfolio_risk: RiskPort | None = None,
        market_truth: MarketTruthService | None = None,
        market_truth_observer: MarketTruthObservationPort | None = None,
    ) -> None:
        self._store = store
        self._broker = broker
        self._market = market
        self._strategy = strategy
        self._underlying = underlying
        self._evidence = evidence
        self._intelligence = intelligence
        self._router = router
        self._portfolio_risk = portfolio_risk
        self._market_truth = market_truth
        self._market_truth_observer = market_truth_observer

    async def run_once(self, *, now: datetime) -> AgentRunResult:
        if any(
            dependency is not None
            for dependency in (
                self._evidence,
                self._intelligence,
                self._router,
                self._portfolio_risk,
            )
        ):
            return await self._run_router_pipeline(now=now)
        return await self._run_legacy_pipeline(now=now)

    async def _run_router_pipeline(self, *, now: datetime) -> AgentRunResult:
        try:
            frame = await self._market.observe(
                self._underlying,
                expiration_gte=now.date() + timedelta(days=12),
                expiration_lte=now.date() + timedelta(days=60),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=AgentDecision(
                    decision_id=sha256_hex(
                        {"pipeline": "router-v1", "underlying": self._underlying}
                    ),
                    action=AgentAction.ABSTAIN,
                    underlying=self._underlying,
                    thesis="No trade: market provider did not produce a valid frame.",
                    invalidation="Re-evaluate on the next fresh market observation.",
                    reasons=("market provider unavailable",),
                    snapshot_hash="market-frame-unavailable",
                ),
            )
        base_decision = self._abstention_decision(frame, "router pipeline is not fully configured")
        if not all(
            dependency is not None
            for dependency in (
                self._evidence,
                self._intelligence,
                self._router,
                self._portfolio_risk,
            )
        ):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=base_decision,
                market_data=frame.market_data,
            )
        assert self._evidence is not None
        assert self._intelligence is not None
        assert self._router is not None
        assert self._portfolio_risk is not None

        if now < frame.observed_at or now - frame.observed_at > _PIPELINE_FRESHNESS:
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "market observation is stale"),
                market_data=frame.market_data,
            )
        if frame.market_data.execution_tier.value == "research_only":
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "market_data_research_only"),
                market_data=frame.market_data,
            )
        account_for_truth: PaperAccount | None = None
        truth: MarketTruthPacket | None = None
        if self._market_truth is not None:
            try:
                account_for_truth = await self._market.get_account()
                observations = (
                    await self._market_truth_observer.observe_market_truth(
                        frame, account_for_truth, observed_at=frame.observed_at
                    )
                    if self._market_truth_observer is not None
                    else ()
                )
                truth = self._market_truth.cross_check(
                    frame=frame,
                    account=account_for_truth,
                    observations=observations,
                    observed_at=frame.observed_at,
                )
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                return AgentRunResult(
                    state=AgentRunState.ABSTAINED,
                    decision=self._abstention_decision(
                        frame, "market_truth_unavailable"
                    ),
                    market_data=frame.market_data,
                )
            if truth.outcome is not MarketTruthOutcome.CORROBORATED:
                return AgentRunResult(
                    state=AgentRunState.ABSTAINED,
                    decision=self._abstention_decision(
                        frame,
                        f"market_truth_{truth.outcome.value}",
                        extra_reasons=truth.reasons,
                    ),
                    market_data=frame.market_data,
                )
        try:
            news = await self._evidence.news(
                frame.underlying, observed_at=frame.observed_at, lookback_hours=48
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "evidence provider unavailable"),
                market_data=frame.market_data,
            )
        if not news:
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "evidence is missing"),
                market_data=frame.market_data,
            )
        try:
            bundle = self._evidence.build_bundle(frame, news)
        except (KeyError, TypeError, ValueError):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "evidence bundle is invalid"),
                market_data=frame.market_data,
            )
        if truth is not None:
            bundle = bundle.model_copy(
                update={
                    "version": "grounded-options-router-v2",
                    "market_truth_hash": truth.truth_hash,
                    "mcp_observation_hashes": truth.mcp_observation_hashes,
                }
            )
        if (
            bundle.observed_at != frame.observed_at
            or now - bundle.observed_at > _PIPELINE_FRESHNESS
        ):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "evidence bundle is stale"),
                market_data=frame.market_data,
            )

        grounded = await self._intelligence.try_analyze(
            bundle, prompt_version=_PROMPT_VERSION
        )
        if grounded.artifact is None:
            reason = grounded.abstention_reason or "grounded thesis unavailable"
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, reason),
                market_data=frame.market_data,
            )
        if not grounded.artifact.valid:
            reasons = grounded.validation_reasons or grounded.artifact.validation_reasons
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(
                    frame,
                    "grounded thesis is invalid",
                    extra_reasons=reasons,
                ),
                market_data=frame.market_data,
            )

        thesis = grounded.artifact.thesis
        quant, router_quant = self._compute_quant_signal(frame)
        try:
            routing = self._router.route(thesis, frame, router_quant)
        except (KeyError, TypeError, ValueError):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "options candidate data is invalid"),
                market_data=frame.market_data,
                quant_snapshot=quant,
            )
        if routing.selected is None:
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(
                    frame,
                    *tuple(routing.reasons) or ("no valid options candidate",),
                ),
                alternatives=tuple(routing.alternatives),
                quant_snapshot=quant,
                market_data=frame.market_data,
            )

        try:
            account = account_for_truth or await self._market.get_account()
            positions = await self._market.get_positions()
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "portfolio provider unavailable"),
                selected_candidate=routing.selected,
                alternatives=tuple(routing.alternatives),
                quant_snapshot=quant,
                market_data=frame.market_data,
            )
        if account.status.upper() != "ACTIVE":
            return AgentRunResult(
                state=AgentRunState.DENIED,
                decision=self._abstention_decision(frame, "paper account is not active"),
                selected_candidate=routing.selected,
                alternatives=tuple(routing.alternatives),
                quant_snapshot=quant,
                market_data=frame.market_data,
            )
        snapshot, snapshot_reason = self._portfolio_snapshot(account, positions, frame.observed_at)
        if snapshot is None:
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, snapshot_reason),
                selected_candidate=routing.selected,
                alternatives=tuple(routing.alternatives),
                quant_snapshot=quant,
                market_data=frame.market_data,
            )
        try:
            risk = self._portfolio_risk.evaluate(routing.selected, thesis, snapshot)
        except (KeyError, TypeError, ValueError):
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=self._abstention_decision(frame, "portfolio risk evaluation unavailable"),
                selected_candidate=routing.selected,
                alternatives=tuple(routing.alternatives),
                quant_snapshot=quant,
                market_data=frame.market_data,
            )
        if risk.outcome.value != "allow":
            return AgentRunResult(
                state=(
                    AgentRunState.ABSTAINED
                    if risk.outcome.value == "abstain"
                    else AgentRunState.DENIED
                ),
                decision=self._abstention_decision(frame, *risk.reasons),
                selected_candidate=routing.selected,
                alternatives=tuple(routing.alternatives),
                quant_snapshot=quant,
                risk_decision=risk,
                market_data=frame.market_data,
            )

        decision = self._decision_from_candidate(
            frame, bundle, thesis, routing.selected
        )
        intent = self._build_intent(decision, frame, account.account_id)
        return await self._execute_authorized_intent(
            now=now,
            frame=frame,
            decision=decision,
            intent=intent,
            account=account,
            selected_candidate=routing.selected,
            alternatives=tuple(routing.alternatives),
            quant_snapshot=quant,
            risk_decision=risk,
            market_data=frame.market_data,
        )

    async def _run_legacy_pipeline(self, *, now: datetime) -> AgentRunResult:
        frame = await self._market.observe(
            self._underlying,
            expiration_gte=now.date() + timedelta(days=12),
            expiration_lte=now.date() + timedelta(days=60),
        )
        decision = self._strategy.decide(frame, now=now)
        if decision.action is AgentAction.ABSTAIN:
            return AgentRunResult(state=AgentRunState.ABSTAINED, decision=decision)

        account = await self._market.get_account()
        if account.status.upper() != "ACTIVE":
            return AgentRunResult(
                state=AgentRunState.DENIED,
                decision=decision.model_copy(
                    update={"reasons": decision.reasons + ("paper account is not active",)}
                ),
            )
        positions = await self._market.get_positions()
        snapshot, snapshot_reason = self._portfolio_snapshot(account, positions, frame.observed_at)
        if snapshot is None:
            return AgentRunResult(
                state=AgentRunState.ABSTAINED,
                decision=decision.model_copy(
                    update={"reasons": decision.reasons + (snapshot_reason,)}
                ),
            )
        if (
            decision.long_symbol is None
            or decision.short_symbol is None
            or decision.limit_price is None
            or decision.max_loss is None
        ):
            raise ValueError("trade decision is missing executable fields")

        legacy_candidate = self._legacy_candidate(decision, frame)
        legacy_thesis = GroundedThesis(
            direction=ThesisDirection.BULLISH,
            confidence=Decimal("1"),
            horizon=Horizon.SWING,
            catalyst="legacy-frame-momentum",
            causal_mechanism="deterministic legacy market-frame momentum",
            beneficiaries=(frame.underlying,),
            adversely_affected=(),
            invalidation=decision.invalidation,
            material_risks=(),
            source_ids=("legacy-market",),
            contradicting_source_ids=(),
        )
        legacy_risk = self._legacy_risk_service().evaluate(
            legacy_candidate, legacy_thesis, snapshot
        )
        if legacy_risk.outcome.value != "allow":
            reasons = decision.reasons + legacy_risk.reasons
            if positions:
                reasons += ("existing underlying exposure is outside portfolio risk budget",)
            return AgentRunResult(
                state=(
                    AgentRunState.ABSTAINED
                    if legacy_risk.outcome.value == "abstain"
                    else AgentRunState.DENIED
                ),
                decision=decision.model_copy(update={"reasons": reasons}),
                risk_decision=legacy_risk,
            )

        intent = self._build_intent(decision, frame, account.account_id)
        capability_policy = evaluate_market_data_policy(frame.market_data, intent)
        if not capability_policy.allowed:
            return AgentRunResult(
                state=AgentRunState.DENIED,
                decision=decision.model_copy(
                    update={"reasons": decision.reasons + capability_policy.reasons}
                ),
                intent_id=intent.intent_id,
                market_data=frame.market_data,
            )
        mandate = Mandate(
            mandate_id="autonomous-paper-agent-v1",
            version=1,
            account_id=account.account_id,
            expires_at=now + timedelta(days=30),
            approval_mode=ApprovalMode.MANDATED,
            allowed_underlyings=frozenset({self._underlying}),
            max_trade_loss=Decimal("250"),
            max_notional=Decimal("1000"),
            autonomous_notional=Decimal("250"),
            required_options_level=3,
        )
        policy = evaluate_policy(
            intent,
            mandate,
            PolicyContext(
                now=now,
                data_fresh=True,
                kill_switch=KillSwitch.OFF,
                broker_options_level=account.options_trading_level,
                projected_notional=intent.max_loss,
                projected_max_loss=intent.max_loss,
            ),
        )
        if policy.outcome is not PolicyOutcome.ALLOW:
            return AgentRunResult(
                state=AgentRunState.DENIED,
                decision=decision.model_copy(
                    update={"reasons": decision.reasons + policy.reasons}
                ),
                intent_id=intent.intent_id,
            )

        return await self._execute_existing_intent(
            now=now, frame=frame, decision=decision, intent=intent, account=account,
            policy=policy, market_data=frame.market_data
        )

    async def _execute_existing_intent(
        self,
        *,
        now: datetime,
        frame: AgentMarketFrame,
        decision: AgentDecision,
        intent: TradeIntent,
        account: PaperAccount,
        policy: Any,
        market_data: MarketDataCapability,
    ) -> AgentRunResult:
        await self._store.create_intent(intent)
        existing_receipt = await self._store.find_receipt(intent.intent_id)
        if existing_receipt is not None:
            return AgentRunResult(
                state=AgentRunState.FILLED,
                decision=decision,
                intent_id=intent.intent_id,
                broker_order_id=existing_receipt.broker_order_id,
                broker_status=existing_receipt.broker_status,
                receipt=existing_receipt,
                market_data=existing_receipt.market_data or market_data,
                paper_accounting=existing_receipt.paper_accounting,
            )
        intent_state = await self._store.get_state(intent.intent_id)
        if intent_state is IntentState.VALIDATED:
            await self._store.authorize_with_reservation(
                intent.intent_id,
                intent.account_id,
                intent.max_loss,
                min(account.buying_power, Decimal("1000")),
            )
        elif intent_state not in {
            IntentState.AUTHORIZED,
            IntentState.SUBMITTING,
            IntentState.REPLACEMENT_PENDING,
            IntentState.PARTIALLY_FILLED,
            IntentState.RECONCILED,
        }:
            return AgentRunResult(
                state=AgentRunState.REJECTED,
                decision=decision,
                intent_id=intent.intent_id,
            )
        order = await ExecutionOrchestrator(self._store, self._broker).execute(intent.intent_id)
        if order.status is BrokerOrderStatus.FILLED:
            receipt = DecisionReceipt(
                receipt_id=f"receipt-{intent.intent_id}",
                intent_id=intent.intent_id,
                operation_hash=intent.operation_hash(),
                evidence_snapshot_hash=intent.data_snapshot_hash,
                mandate_id=intent.mandate_id,
                mandate_version=intent.mandate_version,
                policy_outcome=policy.outcome,
                policy_reasons=policy.reasons,
                broker_order_id=order.order_id,
                broker_status=order.status.value,
                previous_receipt_hash="genesis",
                market_data=market_data,
                paper_accounting=self._paper_accounting(
                    frame=frame, intent=intent, order=order
                ),
            )
            await self._store.save_receipt(receipt)
            return AgentRunResult(
                state=AgentRunState.FILLED,
                decision=decision,
                intent_id=intent.intent_id,
                broker_order_id=order.order_id,
                broker_status=order.status.value,
                receipt=receipt,
                market_data=market_data,
                paper_accounting=receipt.paper_accounting,
            )
        state = (
            AgentRunState.REJECTED
            if order.status in {BrokerOrderStatus.REJECTED, BrokerOrderStatus.CANCELED}
            else AgentRunState.SUBMITTED
        )
        return AgentRunResult(
            state=state,
            decision=decision,
            intent_id=intent.intent_id,
            market_data=market_data,
            broker_order_id=order.order_id,
            broker_status=order.status.value,
        )

    async def _execute_authorized_intent(
        self,
        *,
        now: datetime,
        frame: AgentMarketFrame,
        decision: AgentDecision,
        intent: TradeIntent,
        account: PaperAccount,
        selected_candidate: StrategyCandidate,
        alternatives: tuple[StrategyCandidate, ...],
        quant_snapshot: QuantSignalSnapshot,
        risk_decision: RiskDecision,
        market_data: MarketDataCapability,
    ) -> AgentRunResult:
        capability_policy = evaluate_market_data_policy(market_data, intent)
        common: _RouterRunContext = {
            "selected_candidate": selected_candidate,
            "alternatives": alternatives,
            "quant_snapshot": quant_snapshot,
            "risk_decision": risk_decision,
        }
        if not capability_policy.allowed:
            return AgentRunResult(
                state=AgentRunState.DENIED,
                decision=decision.model_copy(
                    update={"reasons": decision.reasons + capability_policy.reasons}
                ),
                intent_id=intent.intent_id,
                market_data=market_data,
                **common,
            )
        policy = evaluate_policy(
            intent,
            self._mandate(account.account_id, frame.underlying, now),
            PolicyContext(
                now=now,
                data_fresh=True,
                kill_switch=KillSwitch.OFF,
                broker_options_level=account.options_trading_level,
                projected_notional=intent.max_loss,
                projected_max_loss=intent.max_loss,
            ),
        )
        if policy.outcome is not PolicyOutcome.ALLOW:
            return AgentRunResult(
                state=AgentRunState.DENIED,
                decision=decision.model_copy(
                    update={"reasons": decision.reasons + policy.reasons}
                ),
                intent_id=intent.intent_id,
                market_data=market_data,
                **common,
            )
        return await self._persist_and_execute(
            frame=frame,
            intent=intent,
            decision=decision,
            policy=policy,
            account=account,
            market_data=market_data,
            **common,
        )

    async def _persist_and_execute(
        self,
        *,
        frame: AgentMarketFrame,
        intent: TradeIntent,
        decision: AgentDecision,
        policy: Any,
        account: PaperAccount,
        selected_candidate: StrategyCandidate,
        alternatives: tuple[StrategyCandidate, ...],
        quant_snapshot: QuantSignalSnapshot,
        risk_decision: RiskDecision,
        market_data: MarketDataCapability,
    ) -> AgentRunResult:
        await self._store.create_intent(intent)
        existing_receipt = await self._store.find_receipt(intent.intent_id)
        common: _RouterRunContext = {
            "selected_candidate": selected_candidate,
            "alternatives": alternatives,
            "quant_snapshot": quant_snapshot,
            "risk_decision": risk_decision,
        }
        if existing_receipt is not None:
            return AgentRunResult(
                state=AgentRunState.FILLED,
                decision=decision,
                intent_id=intent.intent_id,
                broker_order_id=existing_receipt.broker_order_id,
                broker_status=existing_receipt.broker_status,
                receipt=existing_receipt,
                market_data=existing_receipt.market_data or market_data,
                paper_accounting=existing_receipt.paper_accounting,
                **common,
            )
        intent_state = await self._store.get_state(intent.intent_id)
        if intent_state is IntentState.VALIDATED:
            await self._store.authorize_with_reservation(
                intent.intent_id,
                intent.account_id,
                intent.max_loss,
                min(account.buying_power, Decimal("1000")),
            )
        elif intent_state not in {
            IntentState.AUTHORIZED,
            IntentState.SUBMITTING,
            IntentState.REPLACEMENT_PENDING,
            IntentState.PARTIALLY_FILLED,
            IntentState.RECONCILED,
        }:
            return AgentRunResult(
                state=AgentRunState.REJECTED,
                decision=decision,
                intent_id=intent.intent_id,
                market_data=market_data,
                **common,
            )
        order = await ExecutionOrchestrator(self._store, self._broker).execute(intent.intent_id)
        if order.status is BrokerOrderStatus.FILLED:
            receipt = DecisionReceipt(
                receipt_id=f"receipt-{intent.intent_id}",
                intent_id=intent.intent_id,
                operation_hash=intent.operation_hash(),
                evidence_snapshot_hash=intent.data_snapshot_hash,
                mandate_id=intent.mandate_id,
                mandate_version=intent.mandate_version,
                policy_outcome=policy.outcome,
                policy_reasons=policy.reasons,
                broker_order_id=order.order_id,
                broker_status=order.status.value,
                previous_receipt_hash="genesis",
                market_data=market_data,
                paper_accounting=self._paper_accounting(
                    frame=frame, intent=intent, order=order
                ),
            )
            await self._store.save_receipt(receipt)
            return AgentRunResult(
                state=AgentRunState.FILLED,
                decision=decision,
                intent_id=intent.intent_id,
                broker_order_id=order.order_id,
                broker_status=order.status.value,
                receipt=receipt,
                market_data=market_data,
                paper_accounting=receipt.paper_accounting,
                **common,
            )
        state = (
            AgentRunState.REJECTED
            if order.status in {BrokerOrderStatus.REJECTED, BrokerOrderStatus.CANCELED}
            else AgentRunState.SUBMITTED
        )
        return AgentRunResult(
            state=state,
            decision=decision,
            intent_id=intent.intent_id,
            broker_order_id=order.order_id,
            broker_status=order.status.value,
            market_data=market_data,
            **common,
        )

    @staticmethod
    def _paper_accounting(
        *,
        frame: AgentMarketFrame,
        intent: TradeIntent,
        order: BrokerOrder,
    ) -> PaperExecutionAccounting | None:
        spread_units = intent.legs[0].quantity
        if spread_units != spread_units.to_integral_value():
            return None
        return PaperExecutionAccounting.try_from_open_fill(
            capability=frame.market_data,
            authorized_limit_price=intent.limit_price,
            spread_units=int(spread_units),
            broker_reported_fill_price=order.filled_avg_price,
            broker_reported_filled_qty=order.filled_qty,
        )

    @staticmethod
    def _abstention_decision(
        frame: AgentMarketFrame, reason: str, *, extra_reasons: tuple[str, ...] = ()
    ) -> AgentDecision:
        return AgentDecision(
            decision_id=sha256_hex({"pipeline": "router-v1", "snapshot": frame.snapshot_hash()}),
            action=AgentAction.ABSTAIN,
            underlying=frame.underlying,
            thesis=(
                "No trade: autonomous evidence, quant, strategy, or risk gate "
                "was not satisfied."
            ),
            invalidation="Re-evaluate on the next fresh market observation.",
            reasons=(reason, *extra_reasons),
            snapshot_hash=frame.snapshot_hash(),
        )

    @staticmethod
    def _compute_quant_signal(
        frame: AgentMarketFrame,
    ) -> tuple[QuantSignalSnapshot, QuantSignal]:
        from convictionos.application.quant_signals import compute_quant_signal

        return compute_quant_signal(frame)

    @staticmethod
    def _decision_from_candidate(
        frame: AgentMarketFrame,
        bundle: EvidenceBundle,
        thesis: GroundedThesis,
        candidate: StrategyCandidate,
    ) -> AgentDecision:
        decision_id = sha256_hex(
            {
                "pipeline": "grounded-options-router-v1",
                "frame": frame.snapshot_hash(),
                "evidence": bundle.snapshot_hash(),
                "thesis": thesis.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            }
        )
        return AgentDecision(
            decision_id=decision_id,
            action=AgentAction.TRADE,
            underlying=frame.underlying,
            thesis=thesis.causal_mechanism,
            invalidation=thesis.invalidation,
            reasons=("grounded thesis and deterministic quant agree",),
            snapshot_hash=bundle.snapshot_hash(),
            long_symbol=candidate.long_symbol,
            short_symbol=candidate.short_symbol,
            limit_price=candidate.limit_debit,
            max_loss=candidate.max_loss,
            horizon=candidate.horizon,
        )

    @staticmethod
    def _mandate(account_id: str, underlying: str, now: datetime) -> Mandate:
        return Mandate(
            mandate_id="autonomous-paper-agent-v1",
            version=1,
            account_id=account_id,
            expires_at=now + timedelta(days=30),
            approval_mode=ApprovalMode.MANDATED,
            allowed_underlyings=frozenset({underlying}),
            max_trade_loss=Decimal("250"),
            max_notional=Decimal("1000"),
            autonomous_notional=Decimal("250"),
            required_options_level=3,
        )

    @staticmethod
    def _portfolio_snapshot(
        account: PaperAccount,
        positions: tuple[OpenPosition, ...],
        observed_at: datetime,
    ) -> tuple[PortfolioSnapshot | None, str]:
        exposure: dict[str, Decimal] = {}
        for position in positions:
            if position.quantity == 0:
                continue
            market_value = getattr(position, "market_value", None)
            if market_value is None:
                return None, "portfolio exposure data is unavailable"
            underlying = AutonomousTradingAgent._position_underlying(position.symbol)
            if underlying is None:
                return None, "portfolio exposure data is unavailable"
            exposure[underlying] = exposure.get(underlying, Decimal("0")) + abs(
                Decimal(str(market_value))
            )
        return (
            PortfolioSnapshot(
                observed_at=observed_at,
                open_positions=(),
                active_reservations=(),
                account_equity=Decimal(str(getattr(account, "equity", account.buying_power))),
                buying_power=account.buying_power,
                options_level=account.options_trading_level,
                directional_exposure=exposure,
            ),
            "",
        )

    @staticmethod
    def _position_underlying(symbol: str) -> str | None:
        if re.fullmatch(r"[A-Za-z.]+", symbol):
            return symbol.upper()
        match = _POSITION_SYMBOL.fullmatch(symbol)
        return match.group(1).upper() if match is not None else None

    @staticmethod
    def _legacy_candidate(
        decision: AgentDecision, frame: AgentMarketFrame
    ) -> StrategyCandidate:
        assert decision.long_symbol is not None
        assert decision.short_symbol is not None
        assert decision.limit_price is not None
        assert decision.max_loss is not None
        quotes = {
            quote.symbol: quote
            for quote in frame.calls
            if quote.symbol in {decision.long_symbol, decision.short_symbol}
        }
        long_quote = quotes.get(decision.long_symbol)
        short_quote = quotes.get(decision.short_symbol)
        width = (
            abs(long_quote.strike - short_quote.strike)
            if long_quote is not None and short_quote is not None
            else Decimal("1")
        )
        return StrategyCandidate(
            strategy_id="legacy-defined-risk-call-spread-v1",
            direction=ThesisDirection.BULLISH.value,
            horizon=Horizon.SWING,
            long_symbol=decision.long_symbol,
            short_symbol=decision.short_symbol,
            limit_debit=decision.limit_price,
            max_loss=decision.max_loss,
            width=max(width, Decimal("0.01")),
            dte=60,
            long_delta=Decimal("0.55"),
            liquidity_score=Decimal("0.5"),
            slippage_estimate=Decimal("0"),
            utility_score=Decimal("0.5"),
            rejection_reasons=(),
        )

    @staticmethod
    def _legacy_risk_service() -> PortfolioRiskService:
        budgets = {
            Horizon.CATALYST: HorizonRiskBudget(
                minimum_dte=7,
                maximum_dte=30,
                per_trade_max_loss=Decimal("250"),
                aggregate_capacity=Decimal("1000"),
                max_positions=10,
                confidence_threshold=Decimal("0.7"),
                cooldown_seconds=0,
            ),
            Horizon.SWING: HorizonRiskBudget(
                minimum_dte=30,
                maximum_dte=90,
                per_trade_max_loss=Decimal("250"),
                aggregate_capacity=Decimal("1000"),
                max_positions=10,
                confidence_threshold=Decimal("0.7"),
                cooldown_seconds=0,
            ),
            Horizon.THEMATIC: HorizonRiskBudget(
                minimum_dte=90,
                maximum_dte=450,
                per_trade_max_loss=Decimal("250"),
                aggregate_capacity=Decimal("1000"),
                max_positions=10,
                confidence_threshold=Decimal("0.7"),
                cooldown_seconds=0,
            ),
        }
        return PortfolioRiskService(
            budgets=budgets,
            aggregate_max_loss=Decimal("1000"),
            max_directional_exposure=Decimal("500"),
            minimum_options_level=3,
        )

    @staticmethod
    def _build_intent(
        decision: AgentDecision, frame: AgentMarketFrame, account_id: str
    ) -> TradeIntent:
        assert decision.long_symbol is not None
        assert decision.short_symbol is not None
        assert decision.limit_price is not None
        assert decision.max_loss is not None
        intent_id = f"agent-{decision.decision_id}"
        quantity = Decimal("1")
        return TradeIntent(
            intent_id=intent_id,
            idempotency_key=f"{intent_id}-v1",
            account_id=account_id,
            mandate_id="autonomous-paper-agent-v1",
            mandate_version=1,
            created_at=frame.observed_at,
            expires_at=frame.observed_at + timedelta(minutes=5),
            horizon=decision.horizon or Horizon.SWING,
            underlying=frame.underlying,
            thesis_ref=decision.decision_id,
            legs=(
                TradeLeg(
                    symbol=decision.long_symbol,
                    kind=InstrumentKind.US_OPTION,
                    side=Side.BUY,
                    position_intent=PositionIntent.BUY_TO_OPEN,
                    quantity=quantity,
                    ratio_quantity=1,
                ),
                TradeLeg(
                    symbol=decision.short_symbol,
                    kind=InstrumentKind.US_OPTION,
                    side=Side.SELL,
                    position_intent=PositionIntent.SELL_TO_OPEN,
                    quantity=quantity,
                    ratio_quantity=1,
                ),
            ),
            order_type=OrderType.LIMIT,
            limit_price=decision.limit_price,
            max_loss=decision.max_loss,
            exit_plan=decision.invalidation,
            data_snapshot_hash=decision.snapshot_hash,
        )
