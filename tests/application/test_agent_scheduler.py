import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from convictionos.application.agent_scheduler import (
    AgentScheduler,
    EligibilityGateResult,
    MarketSession,
)
from convictionos.application.position_lifecycle import LifecycleCycleResult
from convictionos.domain.runtime import (
    AgentCycle,
    AgentCycleState,
    RuntimeControlDecision,
    RuntimeControlState,
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self, *, now: datetime) -> object:
        self.calls += 1
        return {"state": "abstained", "at": now.isoformat()}


class FakeClock:
    def __init__(self, session: MarketSession) -> None:
        self.session = session
        self.calls = 0

    async def current_session(self, *, now: datetime) -> MarketSession:
        self.calls += 1
        return self.session


class FakeEligibility:
    def __init__(self, result: EligibilityGateResult) -> None:
        self.result = result
        self.calls = 0

    async def verify_runtime(self, *, now: datetime) -> EligibilityGateResult:
        self.calls += 1
        return self.result


class FakeRuntime:
    def __init__(
        self,
        *,
        decision: RuntimeControlDecision | None = None,
        cycle: AgentCycle | None = None,
    ) -> None:
        self.decision = decision or RuntimeControlDecision(
            control_state=RuntimeControlState.RUNNING,
            public_reasons=(),
            allow_new_entries=True,
            allow_closing_submissions=True,
            control_id=None,
        )
        self.cycle = cycle or AgentCycle(
            cycle_id="cycle-001",
            lease_name="agent:workspace-001:paper-account:convictionos",
            workspace_id="workspace-001",
            account_id="paper-account",
            agent_id="convictionos",
            owner_instance_id="worker-a",
            fencing_token=1,
            release_sha="f" * 40,
            started_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
            state=AgentCycleState.STARTED,
            market_session_state="open",
        )
        self.started: list[tuple[str, datetime | None]] = []
        self.finished: list[tuple[AgentCycleState, str | None, str | None, str | None]] = []

    async def control_decision(self, *, underlying=None):
        return self.decision

    async def start_cycle(self, *, now, market_session_state, next_scheduled_at):
        self.started.append((market_session_state, next_scheduled_at))
        return self.cycle

    async def finish_cycle(
        self,
        cycle,
        *,
        state,
        finished_at,
        safe_error_type=None,
        lifecycle_state=None,
        entry_state=None,
    ):
        del cycle, finished_at
        self.finished.append((state, safe_error_type, lifecycle_state, entry_state))
        return self.cycle.model_copy(update={"state": state, "safe_error_type": safe_error_type})


@pytest.mark.asyncio
async def test_run_now_delegates_and_records_safe_history() -> None:
    now = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    runner = FakeRunner()
    scheduler = AgentScheduler(runner, now=lambda: now, is_market_open=lambda _: True)

    result = await scheduler.run_now()

    assert result["state"] == "abstained"
    assert runner.calls == 1
    assert scheduler.status().last_run_state == "abstained"
    assert len(scheduler.status().runs) == 1


@pytest.mark.asyncio
async def test_pause_blocks_run_now_until_resumed() -> None:
    runner = FakeRunner()
    scheduler = AgentScheduler(
        runner,
        now=lambda: datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        is_market_open=lambda _: True,
    )

    scheduler.pause()

    with pytest.raises(RuntimeError, match="paused"):
        await scheduler.run_now()
    assert runner.calls == 0
    assert scheduler.status().paused is True

    scheduler.resume()
    await scheduler.run_now()
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_tick_skips_closed_market_without_calling_runner() -> None:
    runner = FakeRunner()
    scheduler = AgentScheduler(
        runner,
        now=lambda: datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        is_market_open=lambda _: False,
    )

    result = await scheduler.tick()

    assert result is None
    assert runner.calls == 0
    assert scheduler.status().last_run_state == "skipped"


@pytest.mark.asyncio
async def test_scheduler_uses_injected_alpaca_clock_for_holiday_and_next_cycle() -> None:
    now = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)
    next_open = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
    clock = FakeClock(
        MarketSession(
            is_open=False,
            observed_at=now,
            next_open=next_open,
            next_close=datetime(2026, 9, 8, 20, 0, tzinfo=UTC),
            source="alpaca_clock",
        )
    )
    runner = FakeRunner()
    scheduler = AgentScheduler(runner, now=lambda: now, market_clock=clock)

    result = await scheduler.tick()

    assert result is None
    assert runner.calls == 0
    assert clock.calls == 1
    assert scheduler.status().last_run_state == "skipped"
    assert scheduler.status().last_observation_at == now
    assert scheduler.status().next_cycle_at == next_open


@pytest.mark.asyncio
async def test_closed_alpaca_session_runs_reconciliation_only_without_close_mutation() -> None:
    events: list[str] = []
    now = datetime(2026, 11, 27, 18, 30, tzinfo=UTC)

    class Lifecycle:
        async def run_cycle(self, *, now, allow_close_submission):
            events.append(f"lifecycle:{allow_close_submission}")
            return LifecycleCycleResult(
                observed_at=now,
                open_count=1,
                close_pending_count=1,
                review_count=0,
                block_new_entries=False,
            )

    clock = FakeClock(
        MarketSession(
            is_open=False,
            observed_at=now,
            next_open=datetime(2026, 11, 30, 14, 30, tzinfo=UTC),
            next_close=datetime(2026, 11, 30, 21, 0, tzinfo=UTC),
            source="alpaca_clock",
        )
    )
    runner = FakeRunner()
    scheduler = AgentScheduler(
        runner,
        lifecycle_runner=Lifecycle(),
        now=lambda: now,
        market_clock=clock,
    )

    result = await scheduler.tick()

    assert result is not None
    assert result.state == "reconciliation_only"
    assert runner.calls == 0
    assert events == ["lifecycle:False"]


@pytest.mark.asyncio
async def test_ineligible_runtime_blocks_entry_and_close_submission() -> None:
    events: list[str] = []
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)

    class Lifecycle:
        async def run_cycle(self, *, now, allow_close_submission):
            events.append(f"lifecycle:{allow_close_submission}")
            return LifecycleCycleResult(
                observed_at=now,
                open_count=1,
                close_pending_count=1,
                review_count=0,
                block_new_entries=False,
            )

    runner = FakeRunner()
    eligibility = FakeEligibility(
        EligibilityGateResult(
            eligible=False,
            public_reasons=("MCP schema is required",),
            manifest_hash=None,
            verified_at=now,
        )
    )
    scheduler = AgentScheduler(
        runner,
        lifecycle_runner=Lifecycle(),
        now=lambda: now,
        is_market_open=lambda _: True,
        eligibility_gate=eligibility,
    )

    result = await scheduler.run_now()

    assert result.state == "eligibility_blocked"
    assert runner.calls == 0
    assert events == ["lifecycle:False"]
    assert eligibility.calls == 1
    assert scheduler.status().eligibility == {
        "eligible": False,
        "public_reasons": ("MCP schema is required",),
        "manifest_hash": None,
        "verified_at": now,
    }


@pytest.mark.asyncio
async def test_concurrent_run_is_rejected() -> None:
    started = False

    class BlockingRunner(FakeRunner):
        async def run_once(self, *, now: datetime) -> object:
            nonlocal started
            started = True
            await asyncio.sleep(0.02)
            return await super().run_once(now=now)

    runner = BlockingRunner()
    scheduler = AgentScheduler(
        runner,
        now=lambda: datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        is_market_open=lambda _: True,
    )
    first = asyncio.create_task(scheduler.run_now())
    while not started:
        await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="already running"):
        await scheduler.run_now()
    await first


@pytest.mark.asyncio
async def test_run_notifies_observer_for_background_readiness() -> None:
    runner = FakeRunner()
    observed: list[Any] = []
    scheduler = AgentScheduler(
        runner,
        now=lambda: datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        is_market_open=lambda _: True,
        on_result=observed.append,
    )

    result = await scheduler.run_now()

    assert observed == [result]


@pytest.mark.asyncio
async def test_background_start_runs_immediate_tick_and_records_safe_error() -> None:
    class FailingRunner(FakeRunner):
        async def run_once(self, *, now: datetime) -> object:
            self.calls += 1
            raise ValueError("secret details must not be persisted")

    runner = FailingRunner()
    scheduler = AgentScheduler(
        runner,
        now=lambda: datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        is_market_open=lambda _: True,
    )

    scheduler.start(interval_seconds=60)
    while runner.calls == 0:
        await asyncio.sleep(0)
    await scheduler.stop()

    assert scheduler.status().last_run_state == "error"
    assert scheduler.status().degraded_reasons == ("ValueError",)
    assert scheduler.status().runs[0].reason == "ValueError"


@pytest.mark.asyncio
async def test_lifecycle_runs_before_entry_and_blocks_when_reviewed() -> None:
    events: list[str] = []

    class Lifecycle:
        async def run_cycle(self, *, now, allow_close_submission):
            events.append(f"lifecycle:{allow_close_submission}")
            return LifecycleCycleResult(
                observed_at=now,
                open_count=1,
                close_pending_count=0,
                review_count=1,
                review_reasons=("broker_position_mismatch",),
                block_new_entries=True,
            )

    class Entry(FakeRunner):
        async def run_once(self, *, now):
            events.append("entry")
            return await super().run_once(now=now)

    entry = Entry()
    scheduler = AgentScheduler(
        entry,
        lifecycle_runner=Lifecycle(),
        now=lambda: datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        is_market_open=lambda _: True,
    )

    result = await scheduler.run_now()

    assert result.state == "entry_blocked"
    assert entry.calls == 0
    assert events == ["lifecycle:True"]


@pytest.mark.asyncio
async def test_scheduler_records_durable_cycle_start_and_finish() -> None:
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    runtime = FakeRuntime()
    runner = FakeRunner()
    scheduler = AgentScheduler(
        runner,
        now=lambda: now,
        is_market_open=lambda _: True,
        runtime_coordinator=runtime,
    )

    await scheduler.run_now()

    assert runtime.started == [("open", None)]
    assert runtime.finished == [(AgentCycleState.ENTRY_COMPLETED, None, None, "abstained")]


@pytest.mark.asyncio
async def test_scheduler_records_safe_error_type_in_durable_cycle() -> None:
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    runtime = FakeRuntime()

    class FailingRunner(FakeRunner):
        async def run_once(self, *, now):
            self.calls += 1
            raise ValueError("contains secret")

    scheduler = AgentScheduler(
        FailingRunner(),
        now=lambda: now,
        is_market_open=lambda _: True,
        runtime_coordinator=runtime,
    )

    with pytest.raises(ValueError):
        await scheduler.run_now()

    assert runtime.finished == [(AgentCycleState.ERROR, "ValueError", None, None)]


@pytest.mark.asyncio
async def test_closing_only_runtime_control_blocks_entry_but_allows_close_submission() -> None:
    events: list[str] = []
    runtime = FakeRuntime(
        decision=RuntimeControlDecision(
            control_state=RuntimeControlState.CLOSING_ONLY,
            public_reasons=("risk breaker",),
            allow_new_entries=False,
            allow_closing_submissions=True,
            control_id="control-001",
        )
    )

    class Lifecycle:
        async def run_cycle(self, *, now, allow_close_submission):
            del now
            events.append(f"lifecycle:{allow_close_submission}")
            return LifecycleCycleResult(
                observed_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
                open_count=1,
                close_pending_count=1,
                review_count=0,
                block_new_entries=False,
            )

    runner = FakeRunner()
    scheduler = AgentScheduler(
        runner,
        lifecycle_runner=Lifecycle(),
        now=lambda: datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        is_market_open=lambda _: True,
        runtime_coordinator=runtime,
    )

    result = await scheduler.run_now()

    assert result.state == "entry_blocked"
    assert runner.calls == 0
    assert events == ["lifecycle:True"]
    assert scheduler.status().degraded_reasons == ("risk breaker",)


@pytest.mark.asyncio
async def test_closed_session_runs_lifecycle_without_entry() -> None:
    events: list[str] = []

    class Lifecycle:
        async def run_cycle(self, *, now, allow_close_submission):
            events.append(f"lifecycle:{allow_close_submission}")
            return LifecycleCycleResult(
                observed_at=now,
                open_count=1,
                close_pending_count=1,
                review_count=0,
                block_new_entries=False,
            )

    runner = FakeRunner()
    scheduler = AgentScheduler(
        runner,
        lifecycle_runner=Lifecycle(),
        now=lambda: datetime(2026, 9, 4, 2, 0, tzinfo=UTC),
        is_market_open=lambda _: False,
    )

    result = await scheduler.tick()

    assert result is not None
    assert result.state == "reconciliation_only"
    assert runner.calls == 0
    assert events == ["lifecycle:False"]
