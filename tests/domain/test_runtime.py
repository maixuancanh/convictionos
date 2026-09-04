from datetime import UTC, datetime, timedelta

from convictionos.domain.runtime import (
    RuntimeControl,
    RuntimeControlState,
    RuntimeScopeType,
    SchedulerLease,
)


def test_live_lease_allows_financial_cycle_only_with_minimum_remaining_time() -> None:
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    lease = SchedulerLease(
        lease_name="agent:workspace-001:paper-account",
        owner_instance_id="worker-a",
        fencing_token=7,
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(seconds=46),
        release_sha="f" * 40,
        version=3,
    )

    assert lease.can_start_financial_cycle(now=now) is True
    stale_soon = lease.model_copy(update={"expires_at": now + timedelta(seconds=44)})
    assert stale_soon.can_start_financial_cycle(now=now) is False
    assert lease.can_start_financial_cycle(now=now + timedelta(seconds=47)) is False


def test_runtime_controls_distinguish_entry_and_closing_permissions() -> None:
    paused = RuntimeControl(
        control_id="control-001",
        scope_type=RuntimeScopeType.ACCOUNT,
        scope_id="paper-account",
        state=RuntimeControlState.PAUSED,
        reason="manual review",
        actor="operator",
        policy_version="runtime-policy-v1",
        effective_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
        recovery_condition="operator resumes",
        version=1,
    )
    closing_only = paused.model_copy(
        update={"state": RuntimeControlState.CLOSING_ONLY, "control_id": "control-002"}
    )

    assert paused.allows_new_entries is False
    assert paused.allows_closing_submissions is False
    assert closing_only.allows_new_entries is False
    assert closing_only.allows_closing_submissions is True
