from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from convictionos.application.agent import AgentRunResult
from convictionos.application.market_clock import MarketClockPort, MarketSession
from convictionos.application.position_lifecycle import LifecycleCycleResult
from convictionos.domain.runtime import (
    AgentCycle,
    AgentCycleState,
    RuntimeControlDecision,
    RuntimeControlState,
)


class AgentRunner(Protocol):
    async def run_once(self, *, now: datetime) -> AgentRunResult: ...


class LifecycleRunner(Protocol):
    async def run_cycle(
        self, *, now: datetime, allow_close_submission: bool
    ) -> LifecycleCycleResult: ...


class EligibilityGateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    public_reasons: tuple[str, ...]
    manifest_hash: str | None
    verified_at: datetime | None


class EligibilityGatePort(Protocol):
    async def verify_runtime(self, *, now: datetime) -> EligibilityGateResult: ...


class RuntimeCoordinatorPort(Protocol):
    async def control_decision(
        self, *, underlying: str | None = None
    ) -> RuntimeControlDecision: ...

    async def start_cycle(
        self,
        *,
        now: datetime,
        market_session_state: str,
        next_scheduled_at: datetime | None,
    ) -> AgentCycle: ...

    async def finish_cycle(
        self,
        cycle: AgentCycle,
        *,
        state: AgentCycleState,
        finished_at: datetime,
        safe_error_type: str | None = None,
        lifecycle_state: str | None = None,
        entry_state: str | None = None,
    ) -> AgentCycle: ...


class TradingCycleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lifecycle: LifecycleCycleResult
    entry: AgentRunResult | None = None
    state: str
    eligibility: EligibilityGateResult | None = None


class AgentRunHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at: datetime
    state: str
    reason: str | None = None


class AgentSchedulerStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paused: bool
    running: bool
    last_run_at: datetime | None
    last_run_state: str | None
    runs: tuple[AgentRunHistory, ...]
    last_observation_at: datetime | None = None
    next_cycle_at: datetime | None = None
    eligibility: dict[str, object] | None = None
    degraded_reasons: tuple[str, ...] = ()


class AgentScheduler:
    def __init__(
        self,
        runner: AgentRunner,
        *,
        now: Callable[[], datetime] | None = None,
        is_market_open: Callable[[datetime], bool] | None = None,
        market_clock: MarketClockPort | None = None,
        eligibility_gate: EligibilityGatePort | None = None,
        runtime_coordinator: RuntimeCoordinatorPort | None = None,
        on_result: Callable[[Any], None] | None = None,
        lifecycle_runner: LifecycleRunner | None = None,
        max_history: int = 20,
    ) -> None:
        if max_history < 1:
            raise ValueError("max_history must be positive")
        self._runner = runner
        self._lifecycle_runner = lifecycle_runner
        self._now = now or (lambda: datetime.now(UTC))
        self._is_market_open = is_market_open or (lambda _: True)
        self._market_clock = market_clock
        self._eligibility_gate = eligibility_gate
        self._runtime_coordinator = runtime_coordinator
        self._on_result = on_result
        self._max_history = max_history
        self._paused = False
        self._history: list[AgentRunHistory] = []
        self._last_session: MarketSession | None = None
        self._last_eligibility: EligibilityGateResult | None = None
        self._degraded_reasons: tuple[str, ...] = ()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def status(self) -> AgentSchedulerStatus:
        return AgentSchedulerStatus(
            paused=self._paused,
            running=self._lock.locked(),
            last_run_at=self._history[-1].started_at if self._history else None,
            last_run_state=self._history[-1].state if self._history else None,
            runs=tuple(reversed(self._history)),
            last_observation_at=(
                self._last_session.observed_at if self._last_session is not None else None
            ),
            next_cycle_at=(
                self._last_session.next_close
                if self._last_session is not None and self._last_session.is_open
                else self._last_session.next_open
                if self._last_session is not None
                else None
            ),
            eligibility=(
                self._last_eligibility.model_dump(mode="python")
                if self._last_eligibility is not None
                else None
            ),
            degraded_reasons=self._degraded_reasons,
        )

    def set_result_observer(self, observer: Callable[[Any], None]) -> None:
        self._on_result = observer

    def pause(self) -> AgentSchedulerStatus:
        self._paused = True
        return self.status()

    def resume(self) -> AgentSchedulerStatus:
        self._paused = False
        return self.status()

    async def tick(self) -> AgentRunResult | TradingCycleResult | None:
        if self._paused:
            return None
        started_at = self._now()
        market_open = await self._market_open(started_at)
        if not market_open and self._lifecycle_runner is None:
            self._record(
                AgentRunHistory(
                    started_at=started_at, state="skipped", reason="market closed"
                )
            )
            return None
        return await self._run(started_at, market_open=market_open)

    async def run_now(self) -> AgentRunResult | TradingCycleResult:
        if self._paused:
            raise RuntimeError("agent scheduler is paused")
        started_at = self._now()
        return await self._run(started_at, market_open=await self._market_open(started_at))

    def start(self, *, interval_seconds: float = 300.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(interval_seconds))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self, interval_seconds: float) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._degraded_reasons = (type(error).__name__,)
            await asyncio.sleep(interval_seconds)

    async def _run(
        self, started_at: datetime, *, market_open: bool = True
    ) -> AgentRunResult | TradingCycleResult:
        if self._lock.locked():
            raise RuntimeError("agent scheduler run is already running")
        async with self._lock:
            cycle: AgentCycle | None = None
            try:
                runtime_decision = await self._runtime_decision()
                cycle = await self._start_runtime_cycle(started_at)
                eligibility = await self._verify_eligibility(started_at)
                eligibility_allows_mutation = (
                    eligibility is None or eligibility.eligible
                )
                allow_close_submission = (
                    market_open
                    and eligibility_allows_mutation
                    and runtime_decision.allow_closing_submissions
                )
                allow_new_entries = (
                    market_open
                    and eligibility_allows_mutation
                    and runtime_decision.allow_new_entries
                )
                result: AgentRunResult | TradingCycleResult
                if self._lifecycle_runner is None:
                    if eligibility is not None and not eligibility.eligible:
                        result = TradingCycleResult(
                            lifecycle=LifecycleCycleResult(
                                observed_at=started_at,
                                open_count=0,
                                close_pending_count=0,
                                review_count=0,
                                review_reasons=eligibility.public_reasons,
                                block_new_entries=True,
                            ),
                            entry=None,
                            state="eligibility_blocked",
                            eligibility=eligibility,
                        )
                    elif not runtime_decision.allow_new_entries:
                        result = TradingCycleResult(
                            lifecycle=LifecycleCycleResult(
                                observed_at=started_at,
                                open_count=0,
                                close_pending_count=0,
                                review_count=0,
                                review_reasons=runtime_decision.public_reasons,
                                block_new_entries=True,
                            ),
                            entry=None,
                            state="entry_blocked",
                            eligibility=eligibility,
                        )
                    else:
                        result = await self._runner.run_once(now=started_at)
                else:
                    lifecycle = await self._lifecycle_runner.run_cycle(
                        now=started_at, allow_close_submission=allow_close_submission
                    )
                    entry = None
                    if allow_new_entries and not lifecycle.block_new_entries:
                        entry = await self._runner.run_once(now=started_at)
                    result = TradingCycleResult(
                        lifecycle=lifecycle,
                        entry=entry,
                        state=(
                            "entry_completed"
                            if entry is not None
                            else "eligibility_blocked"
                            if market_open
                            and eligibility is not None
                            and not eligibility.eligible
                            else "entry_blocked"
                            if market_open
                            and (
                                lifecycle.block_new_entries
                                or not runtime_decision.allow_new_entries
                            )
                            else "reconciliation_only"
                        ),
                        eligibility=eligibility,
                    )
            except Exception as error:
                self._degraded_reasons = (type(error).__name__,)
                self._record(
                    AgentRunHistory(
                        started_at=started_at,
                        state="error",
                        reason=type(error).__name__,
                    )
                )
                if cycle is not None:
                    await self._finish_runtime_cycle(
                        cycle,
                        state=AgentCycleState.ERROR,
                        safe_error_type=type(error).__name__,
                    )
                raise
            state = _state(result)
            self._record(AgentRunHistory(started_at=started_at, state=state))
            if cycle is not None:
                await self._finish_runtime_cycle(
                    cycle,
                    state=_cycle_state(result),
                    lifecycle_state=state if isinstance(result, TradingCycleResult) else None,
                    entry_state=(
                        _state(result.entry)
                        if isinstance(result, TradingCycleResult) and result.entry is not None
                        else _state(result)
                        if not isinstance(result, TradingCycleResult)
                        else None
                    ),
                )
            if self._on_result is not None:
                self._on_result(result)
            return result

    async def _market_open(self, now: datetime) -> bool:
        if self._market_clock is None:
            self._last_session = MarketSession(
                is_open=self._is_market_open(now),
                observed_at=now,
                source="local_fallback",
            )
            return self._last_session.is_open
        self._last_session = await self._market_clock.current_session(now=now)
        return self._last_session.is_open

    async def _verify_eligibility(self, now: datetime) -> EligibilityGateResult | None:
        if self._eligibility_gate is None:
            return None
        self._last_eligibility = await self._eligibility_gate.verify_runtime(now=now)
        return self._last_eligibility

    async def _runtime_decision(self) -> RuntimeControlDecision:
        if self._runtime_coordinator is None:
            return RuntimeControlDecision(
                control_state=RuntimeControlState.RUNNING,
                public_reasons=(),
                allow_new_entries=True,
                allow_closing_submissions=True,
                control_id=None,
            )
        decision = await self._runtime_coordinator.control_decision()
        self._degraded_reasons = (
            decision.public_reasons
            if decision.control_state is not RuntimeControlState.RUNNING
            else ()
        )
        return decision

    async def _start_runtime_cycle(self, started_at: datetime) -> AgentCycle | None:
        if self._runtime_coordinator is None:
            return None
        session = self._last_session
        return await self._runtime_coordinator.start_cycle(
            now=started_at,
            market_session_state=(
                "open" if session is not None and session.is_open else "closed"
            ),
            next_scheduled_at=(
                session.next_close
                if session is not None and session.is_open
                else session.next_open
                if session is not None
                else None
            ),
        )

    async def _finish_runtime_cycle(
        self,
        cycle: AgentCycle,
        *,
        state: AgentCycleState,
        safe_error_type: str | None = None,
        lifecycle_state: str | None = None,
        entry_state: str | None = None,
    ) -> None:
        if self._runtime_coordinator is None:
            return
        await self._runtime_coordinator.finish_cycle(
            cycle,
            state=state,
            finished_at=self._now(),
            safe_error_type=safe_error_type,
            lifecycle_state=lifecycle_state,
            entry_state=entry_state,
        )

    def _record(self, entry: AgentRunHistory) -> None:
        self._history.append(entry)
        del self._history[:-self._max_history]


def _state(result: Any) -> str:
    if isinstance(result, TradingCycleResult):
        return result.state
    value = result.get("state") if isinstance(result, dict) else getattr(result, "state", None)
    return getattr(value, "value", str(value))


def _cycle_state(result: AgentRunResult | TradingCycleResult) -> AgentCycleState:
    if isinstance(result, TradingCycleResult):
        try:
            return AgentCycleState(result.state)
        except ValueError:
            return AgentCycleState.ENTRY_BLOCKED
    return AgentCycleState.ENTRY_COMPLETED
