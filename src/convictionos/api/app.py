import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol, cast

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.routers.health import create_health_router
from convictionos.api.schemas.eligibility import PublicCompetitionReadiness
from convictionos.api.templates import (
    dashboard_page,
    decisions_page,
    landing_page,
    proof_page,
    research_page,
)
from convictionos.application.agent import AgentRunResult
from convictionos.application.agent_scheduler import AgentScheduler, TradingCycleResult
from convictionos.application.control_plane import ControlPlaneService
from convictionos.application.demo import DemoService
from convictionos.application.health import HealthService
from convictionos.application.incidents import IncidentService, StaleIncidentVersion
from convictionos.domain.eligibility import EligibilityManifest
from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.market_data import MarketDataCapability
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.runtime import RuntimeStatus
from convictionos.domain.trading import Horizon
from convictionos.infrastructure.brokers import BrokerPort
from convictionos.infrastructure.db import build_session_factory
from convictionos.infrastructure.store import Store

STATIC_DIRECTORY = Path(__file__).parent / "static"
PUBLIC_WEB_ORIGINS = (
    "https://convictionos-web.vercel.app",
    "http://localhost:4177",
    "http://127.0.0.1:4177",
)


class DemoRunRequest(BaseModel):
    long_option_symbol: str
    short_option_symbol: str
    limit_price: Decimal = Field(gt=0)


class MutationRequest(BaseModel):
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal = Field(gt=0)


class ResolveIncidentRequest(BaseModel):
    expected_version: int = Field(ge=1)
    recovery_evidence_hash: str = Field(min_length=64, max_length=64)


class AgentRunnerPort(Protocol):
    async def run_once(self, *, now: datetime) -> AgentRunResult: ...


class RuntimeStatusPort(Protocol):
    async def status(self) -> RuntimeStatus: ...


class ReadinessCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str
    direction: str
    horizon: Horizon
    long_symbol: str
    short_symbol: str
    max_loss: Decimal
    expiry_dte: int
    liquidity: Decimal


class ReadinessRejectedCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str


class ReadinessRiskBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["allow", "deny", "abstain"]
    horizon: Horizon
    projected_max_loss: Decimal
    reasons: tuple[str, ...]


class StrategyReadiness(BaseModel):
    """Safe, presentation-only projection of the latest paper run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["unavailable", "abstained", "denied", "submitted", "filled", "rejected"]
    provider_thesis_status: Literal["unavailable", "validated", "abstained"]
    direction: ThesisDirection | None
    horizon: Horizon | None
    ai_quant_agreement: Literal["confirmed", "conflict", "unavailable"]
    candidate_outcome: Literal["unavailable", "selected", "abstained"]
    selected_candidate: ReadinessCandidate | None
    rejected_candidates: tuple[ReadinessRejectedCandidate, ...]
    max_loss: Decimal | None
    expiry: date | None
    expiry_dte: int | None
    liquidity: Decimal | None
    portfolio_risk_budget: ReadinessRiskBudget | None
    evidence_freshness: Literal["unavailable", "fresh", "stale"]
    abstention_reason: str | None
    paper_only: Literal[True] = True
    live_trading_authorized: Literal[False] = False
    underlying_feed: Literal["iex", "sip"] | None
    options_feed: Literal["indicative", "opra", "unknown"] | None
    market_data_tier: Literal["unavailable", "research_only", "paper_executable"]
    max_spread_units: int | None
    performance_eligible: bool
    market_data_limitations: tuple[str, ...]
    accounting_state: Literal["unavailable", "open_fill_only", "closed"]
    authorized_cost_ceiling: Decimal | None
    broker_reported_entry_cost: Decimal | None
    conservative_realized_pnl: Decimal | None
    lifecycle_summary: "PositionLifecycleSummary | None" = None


class PublicPositionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    public_id: str
    underlying: str
    horizon: Horizon
    direction: ThesisDirection
    expiry_dte: int
    lifecycle_state: str
    exit_policy_version: str
    latest_exit_outcome: str | None
    latest_exit_reasons: tuple[str, ...]
    quote_freshness: str
    broker_gross_pnl: Decimal | None
    conservative_gross_pnl: Decimal | None
    verified_fees: Decimal | None
    verified_after_cost_pnl: Decimal | None


class PositionLifecycleSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    open: int
    close_pending: int
    review: int
    closed: int
    positions: tuple[PublicPositionView, ...]


_OCC_EXPIRY = re.compile(r"(?P<expiry>\d{6})[CP]\d{8}$")


def _candidate_expiry(candidate: object) -> date | None:
    for field in ("long_symbol", "short_symbol"):
        symbol = getattr(candidate, field, "")
        match = _OCC_EXPIRY.search(symbol)
        if match is None:
            continue
        value = match.group("expiry")
        try:
            return date(2000 + int(value[:2]), int(value[2:4]), int(value[4:6]))
        except ValueError:
            return None
    return None


def _readiness_from_run_result(
    result: AgentRunResult | TradingCycleResult | None,
) -> StrategyReadiness:
    if isinstance(result, TradingCycleResult):
        result = result.entry
    if result is None:
        return StrategyReadiness(
            status="unavailable",
            provider_thesis_status="unavailable",
            direction=None,
            horizon=None,
            ai_quant_agreement="unavailable",
            candidate_outcome="unavailable",
            selected_candidate=None,
            rejected_candidates=(),
            max_loss=None,
            expiry=None,
            expiry_dte=None,
            liquidity=None,
            portfolio_risk_budget=None,
            evidence_freshness="unavailable",
            abstention_reason="no agent run result available",
            underlying_feed=None,
            options_feed=None,
            market_data_tier="unavailable",
            max_spread_units=None,
            performance_eligible=False,
            market_data_limitations=(),
            accounting_state="unavailable",
            authorized_cost_ceiling=None,
            broker_reported_entry_cost=None,
            conservative_realized_pnl=None,
        )

    receipt = result.receipt
    market_data: MarketDataCapability | None = result.market_data or (
        receipt.market_data if receipt is not None else None
    )
    accounting: PaperExecutionAccounting | None = result.paper_accounting or (
        receipt.paper_accounting if receipt is not None else None
    )

    reasons = result.decision.reasons
    lowered_reasons = tuple(reason.lower() for reason in reasons)
    has_provider_failure = any(
        "intelligence" in reason
        or "grounded thesis" in reason
        or "evidence" in reason
        for reason in lowered_reasons
    )
    provider_status: Literal["validated", "abstained"] = (
        "abstained" if has_provider_failure else "validated"
    )
    agreement: Literal["confirmed", "conflict", "unavailable"] = "unavailable"
    if result.selected_candidate is not None and result.quant_snapshot is not None:
        agreement = "confirmed"
    elif any("direction conflict" in reason for reason in lowered_reasons):
        agreement = "conflict"

    selected = result.selected_candidate
    selected_view = (
        ReadinessCandidate(
            strategy_id=selected.strategy_id,
            direction=selected.direction,
            horizon=selected.horizon,
            long_symbol=selected.long_symbol,
            short_symbol=selected.short_symbol,
            max_loss=selected.max_loss,
            expiry_dte=selected.dte,
            liquidity=selected.liquidity_score,
        )
        if selected is not None
        else None
    )
    risk = result.risk_decision
    risk_view = (
        ReadinessRiskBudget(
            outcome=risk.outcome.value,
            horizon=risk.horizon,
            projected_max_loss=risk.projected_max_loss,
            reasons=risk.reasons,
        )
        if risk is not None
        else None
    )
    rejected = (
        tuple(ReadinessRejectedCandidate(reason=reason) for reason in reasons)
        if selected is None
        else ()
    )
    has_stale_reason = any("stale" in reason for reason in lowered_reasons)
    freshness: Literal["fresh", "stale"] = "stale" if has_stale_reason else "fresh"
    accounting_state: Literal["unavailable", "open_fill_only", "closed"] = "unavailable"
    if accounting is not None:
        accounting_state = cast(Literal["open_fill_only"], accounting.state.value)
    return StrategyReadiness(
        status=result.state.value,
        provider_thesis_status=provider_status,
        direction=(ThesisDirection(selected.direction) if selected is not None else None),
        horizon=(selected.horizon if selected is not None else (risk.horizon if risk else None)),
        ai_quant_agreement=agreement,
        candidate_outcome="selected" if selected is not None else "abstained",
        selected_candidate=selected_view,
        rejected_candidates=rejected,
        max_loss=selected.max_loss if selected is not None else None,
        expiry=_candidate_expiry(selected) if selected is not None else None,
        expiry_dte=selected.dte if selected is not None else None,
        liquidity=selected.liquidity_score if selected is not None else None,
        portfolio_risk_budget=risk_view,
        evidence_freshness=freshness,
        abstention_reason=(
            reasons[0] if result.state.value in {"abstained", "denied"} and reasons else None
        ),
        underlying_feed=market_data.underlying_feed.value if market_data is not None else None,
        options_feed=market_data.options_feed.value if market_data is not None else None,
        market_data_tier=(
            market_data.execution_tier.value if market_data is not None else "unavailable"
        ),
        max_spread_units=market_data.max_spread_units if market_data is not None else None,
        performance_eligible=(
            market_data.performance_eligible if market_data is not None else False
        ),
        market_data_limitations=(
            tuple(limitation.value for limitation in market_data.limitations)
            if market_data is not None
            else ()
        ),
        accounting_state=accounting_state,
        authorized_cost_ceiling=(
            accounting.authorized_cost_ceiling if accounting is not None else None
        ),
        broker_reported_entry_cost=(
            accounting.broker_reported_cost_basis if accounting is not None else None
        ),
        conservative_realized_pnl=(
            accounting.conservative_realized_pnl if accounting is not None else None
        ),
    )


def create_app(
    *,
    engine: AsyncEngine,
    broker: BrokerPort,
    broker_mode: str = "fake",
    agent: AgentRunnerPort | None = None,
    agent_enabled: bool = False,
    agent_control_token: str = "",
    scheduler: AgentScheduler | None = None,
    runtime_coordinator: RuntimeStatusPort | None = None,
    scheduler_background: bool = False,
    scheduler_interval_seconds: float = 300.0,
    runtime_release_sha: str = "unknown",
    cli_revision: str | None = None,
    mcp_version: str | None = None,
    degraded_reasons: tuple[str, ...] = (),
    eligibility_manifest: EligibilityManifest | None = None,
    eligibility_capture: Callable[
        [str, bool, datetime], Awaitable[EligibilityManifest]
    ]
    | None = None,
    competition_workspace_id: str = "",
    competition_account_id: str = "",
    include_demo_routes: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if scheduler_background and agent_enabled and active_scheduler is not None:
            active_scheduler.start(interval_seconds=scheduler_interval_seconds)
        try:
            yield
        finally:
            if active_scheduler is not None:
                await active_scheduler.stop()

    app = FastAPI(title="ConvictionOS", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(PUBLIC_WEB_ORIGINS),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Agent-Control-Token"],
    )
    app.state.scheduler_background = scheduler_background
    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    app.include_router(
        create_health_router(
            HealthService(engine, release_sha=runtime_release_sha),
            release_sha=runtime_release_sha,
        )
    )
    store = Store(build_session_factory(engine))
    service = DemoService(store, broker)
    incident_service = IncidentService.for_engine(engine)
    control_plane = ControlPlaneService(
        store, broker_mode=broker_mode
    )
    latest_run_result: AgentRunResult | None = None
    active_scheduler = scheduler or (AgentScheduler(agent) if agent is not None else None)

    async def public_positions_summary() -> PositionLifecycleSummary:
        managed = await store.list_managed_positions()
        review_states = {"assignment_review", "expiration_review", "reconciliation_failed"}
        views = tuple(
            PublicPositionView(
                public_id=position.position_id,
                underlying=re.split(r"\d", position.long_leg.symbol, maxsplit=1)[0],
                horizon=position.horizon,
                direction=position.direction,
                expiry_dte=(position.expiration - datetime.now().date()).days,
                lifecycle_state=position.state.value,
                exit_policy_version=position.exit_policy_version,
                latest_exit_outcome=None,
                latest_exit_reasons=(),
                quote_freshness="unavailable",
                broker_gross_pnl=None,
                conservative_gross_pnl=None,
                verified_fees=None,
                verified_after_cost_pnl=None,
            )
            for position in managed
        )
        return PositionLifecycleSummary(
            open=sum(position.lifecycle_state == "open" for position in views),
            close_pending=sum(
                position.lifecycle_state == "close_pending" for position in views
            ),
            review=sum(position.lifecycle_state in review_states for position in views),
            closed=sum(position.lifecycle_state == "closed_reconciled" for position in views),
            positions=views,
        )

    def observe_agent_result(result: AgentRunResult | TradingCycleResult) -> None:
        nonlocal latest_run_result
        if isinstance(result, TradingCycleResult):
            if result.entry is not None:
                latest_run_result = result.entry
        else:
            latest_run_result = result

    if active_scheduler is not None:
        active_scheduler.set_result_observer(observe_agent_result)

    def require_control_token(x_agent_control_token: str) -> None:
        if (
            not agent_control_token
            or not secrets.compare_digest(x_agent_control_token, agent_control_token)
        ):
            raise HTTPException(status_code=403, detail="invalid agent control token")

    def scheduler_status() -> dict[str, object]:
        if active_scheduler is None:
            return {
                "scheduler_running": False,
                "scheduler_paused": False,
                "last_run_at": None,
                "last_run_state": None,
                "runs": [],
                "eligibility": None,
                "last_observation_at": None,
                "next_cycle_at": None,
                "runtime_release_sha": runtime_release_sha,
                "cli_revision": cli_revision,
                "mcp_version": mcp_version,
                "degraded_reasons": list(degraded_reasons),
            }
        status = active_scheduler.status()
        return {
            "scheduler_running": status.running,
            "scheduler_paused": status.paused,
            "last_run_at": status.last_run_at,
            "last_run_state": status.last_run_state,
            "runs": [run.model_dump(mode="json") for run in status.runs],
            "eligibility": status.eligibility,
            "last_observation_at": status.last_observation_at,
            "next_cycle_at": status.next_cycle_at,
            "runtime_release_sha": runtime_release_sha,
            "cli_revision": cli_revision,
            "mcp_version": mcp_version,
            "degraded_reasons": list(status.degraded_reasons or degraded_reasons),
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "release_sha": runtime_release_sha}

    async def load_eligibility_manifest(workspace_id: str) -> EligibilityManifest | None:
        manifest = eligibility_manifest
        if manifest is not None:
            if not workspace_id or manifest.workspace_id == workspace_id:
                return manifest
            return None
        if not workspace_id or not competition_account_id:
            return None
        return await store.latest_eligibility_manifest(
            workspace_id, competition_account_id
        )

    def manifest_public_summary(manifest: EligibilityManifest) -> PublicCompetitionReadiness:
        return PublicCompetitionReadiness(
            outcome=manifest.outcome.value,
            options_incorporated=manifest.options_level >= 3,
            cli_revision=manifest.cli_revision,
            mcp_version=manifest.mcp_version,
            options_feed=manifest.option_feed.value,
            market_data_tier=manifest.market_data_capability.execution_tier.value,
            mandate_version=manifest.mandate_version,
            deployed_sha=manifest.deployed_git_sha,
            verified_at=manifest.verified_at,
            public_reasons=manifest.public_reasons,
        )

    async def public_competition_readiness() -> PublicCompetitionReadiness:
        manifest = await load_eligibility_manifest(competition_workspace_id)
        if manifest is None:
            return PublicCompetitionReadiness(
                outcome="unavailable",
                options_incorporated=False,
                cli_revision=cli_revision,
                mcp_version=mcp_version,
                options_feed=None,
                market_data_tier=None,
                mandate_version=None,
                deployed_sha=runtime_release_sha if runtime_release_sha != "unknown" else None,
                verified_at=None,
                public_reasons=("eligibility manifest unavailable",),
            )
        return manifest_public_summary(manifest)

    @app.get("/v1/public/competition-readiness", response_model=PublicCompetitionReadiness)
    async def public_competition_readiness_route() -> PublicCompetitionReadiness:
        return await public_competition_readiness()

    @app.get("/v1/workspaces/{workspace_id}/eligibility")
    async def workspace_eligibility(
        workspace_id: str,
        x_agent_control_token: str = Header(default=""),
    ) -> dict[str, object]:
        require_control_token(x_agent_control_token)
        manifest = await load_eligibility_manifest(workspace_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="eligibility manifest not found")
        return manifest.model_dump(mode="json")

    @app.post(
        "/v1/workspaces/{workspace_id}/eligibility/capture-baseline",
        response_model=PublicCompetitionReadiness,
    )
    async def capture_workspace_eligibility_baseline(
        workspace_id: str,
        x_agent_control_token: str = Header(default=""),
    ) -> PublicCompetitionReadiness:
        require_control_token(x_agent_control_token)
        if eligibility_capture is None:
            raise HTTPException(status_code=503, detail="eligibility capture unavailable")
        manifest = await eligibility_capture(workspace_id, True, datetime.now(UTC))
        return manifest_public_summary(manifest)

    @app.get("/v1/workspaces/{workspace_id}/incidents")
    async def workspace_incidents(workspace_id: str) -> dict[str, object]:
        return {"incidents": await incident_service.list_public(workspace_id)}

    @app.post("/v1/workspaces/{workspace_id}/incidents/{incident_id}/resolve")
    async def resolve_workspace_incident(
        workspace_id: str,
        incident_id: str,
        request: ResolveIncidentRequest,
        x_agent_control_token: str = Header(default=""),
    ) -> dict[str, object]:
        del workspace_id
        require_control_token(x_agent_control_token)
        try:
            incident = await incident_service.resolve(
                incident_id,
                actor="operator",
                expected_version=request.expected_version,
                resolved_at=datetime.now(),
                recovery_evidence_hash=request.recovery_evidence_hash,
            )
        except StaleIncidentVersion as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "public_id": incident.public_id,
            "state": incident.state.value,
            "version": incident.version,
        }

    @app.get("/v1/control-plane")
    async def control_plane_state() -> object:
        summary = await control_plane.summary()
        lifecycle = await public_positions_summary()
        readiness = _readiness_from_run_result(latest_run_result).model_copy(
            update={"lifecycle_summary": lifecycle}
        )
        return {
            **summary.model_dump(mode="json"),
            "agent_readiness": readiness.model_dump(mode="json"),
            "lifecycle": lifecycle.model_dump(mode="json"),
        }

    @app.get("/v1/strategy/readiness", response_model=StrategyReadiness)
    @app.get("/v1/agent/readiness", response_model=StrategyReadiness, include_in_schema=False)
    async def strategy_readiness() -> StrategyReadiness:
        return _readiness_from_run_result(latest_run_result).model_copy(
            update={"lifecycle_summary": await public_positions_summary()}
        )

    @app.get("/v1/positions", response_model=PositionLifecycleSummary)
    async def positions() -> PositionLifecycleSummary:
        return await public_positions_summary()

    @app.get("/v1/agent/status")
    async def agent_status() -> dict[str, object]:
        return {
            "enabled": agent_enabled,
            "configured": agent is not None,
            "broker_mode": broker_mode,
            "live_trading_authorized": False,
            **scheduler_status(),
        }

    @app.get("/v1/runtime/status")
    async def runtime_status() -> dict[str, object]:
        if runtime_coordinator is None:
            return {
                "configured": False,
                "worker_live": False,
                "lease_owner_instance_id": None,
                "lease_expires_at": None,
                "last_heartbeat_at": None,
                "last_cycle_state": None,
                "effective_control_state": "running",
                "runtime_release_sha": runtime_release_sha,
            }
        status = await runtime_coordinator.status()
        now = datetime.now(UTC)
        return {
            "configured": True,
            "worker_live": (
                status.lease_expires_at is not None and status.lease_expires_at > now
            ),
            "lease_owner_instance_id": status.lease_owner_instance_id,
            "lease_expires_at": status.lease_expires_at,
            "last_heartbeat_at": status.last_heartbeat_at,
            "last_cycle_state": status.last_cycle_state,
            "effective_control_state": status.effective_control_state.value,
            "runtime_release_sha": runtime_release_sha,
        }

    @app.post("/v1/agent/run-once")
    async def run_agent_once(
        x_agent_control_token: str = Header(default=""),
    ) -> AgentRunResult | TradingCycleResult:
        nonlocal latest_run_result
        if not agent_enabled or agent is None:
            raise HTTPException(status_code=503, detail="paper agent is disabled or unconfigured")
        require_control_token(x_agent_control_token)
        if active_scheduler is None:
            raise HTTPException(status_code=503, detail="paper agent is disabled or unconfigured")
        try:
            cycle_result = await active_scheduler.run_now()
            observe_agent_result(cycle_result)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return cycle_result

    @app.post("/v1/agent/scheduler/pause")
    async def pause_agent_scheduler(
        x_agent_control_token: str = Header(default=""),
    ) -> dict[str, object]:
        require_control_token(x_agent_control_token)
        if active_scheduler is None:
            raise HTTPException(status_code=503, detail="paper agent is disabled or unconfigured")
        active_scheduler.pause()
        return scheduler_status()

    @app.post("/v1/agent/scheduler/resume")
    async def resume_agent_scheduler(
        x_agent_control_token: str = Header(default=""),
    ) -> dict[str, object]:
        require_control_token(x_agent_control_token)
        if active_scheduler is None:
            raise HTTPException(status_code=503, detail="paper agent is disabled or unconfigured")
        active_scheduler.resume()
        return scheduler_status()

    @app.post("/v1/agent/scheduler/run-now")
    async def run_scheduler_now(
        x_agent_control_token: str = Header(default=""),
    ) -> AgentRunResult | TradingCycleResult:
        return await run_agent_once(x_agent_control_token)

    @app.get("/", response_class=HTMLResponse)
    async def landing() -> str:
        return landing_page(await control_plane.summary())

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        return dashboard_page(
            await control_plane.summary(),
            agent_enabled=agent_enabled,
            agent_configured=agent is not None,
            readiness=_readiness_from_run_result(latest_run_result),
        )

    @app.get("/decisions", response_class=HTMLResponse)
    async def decisions() -> str:
        return decisions_page(await control_plane.summary())

    @app.get("/research", response_class=HTMLResponse)
    async def research() -> str:
        return research_page(await control_plane.summary())

    @app.get("/proof", response_class=HTMLResponse)
    async def proof() -> str:
        return proof_page(await control_plane.summary())

    if include_demo_routes:

        @app.post("/v1/demo/run")
        async def run_demo(request: DemoRunRequest) -> dict[str, str]:
            receipt = await service.run(
                request.long_option_symbol,
                request.short_option_symbol,
                request.limit_price,
            )
            return {
                "intent_id": receipt.intent_id,
                "policy_outcome": receipt.policy_outcome.value,
                "broker_order_id": receipt.broker_order_id,
                "broker_status": receipt.broker_status,
                "receipt_hash": receipt.receipt_hash(),
            }

        @app.post("/v1/demo/evaluate-mutation")
        async def evaluate_mutation(request: MutationRequest) -> dict[str, object]:
            decision, original_hash, mutated_hash = service.evaluate_mutation(
                request.quantity, request.limit_price
            )
            return {
                "outcome": decision.outcome.value,
                "reasons": list(decision.reasons),
                "operation_hash_changed": original_hash != mutated_hash,
            }

    return app
