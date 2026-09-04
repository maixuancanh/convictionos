from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MINIMUM_FINANCIAL_CYCLE_LEASE_SECONDS = 45


class RuntimeControlState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    CLOSING_ONLY = "closing_only"
    FROZEN_REVIEW = "frozen_review"


class RuntimeScopeType(StrEnum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    ACCOUNT = "account"
    AGENT = "agent"
    UNDERLYING = "underlying"


class AgentCycleState(StrEnum):
    STARTED = "started"
    ENTRY_COMPLETED = "entry_completed"
    ENTRY_BLOCKED = "entry_blocked"
    ELIGIBILITY_BLOCKED = "eligibility_blocked"
    RECONCILIATION_ONLY = "reconciliation_only"
    SKIPPED = "skipped"
    ERROR = "error"


class SchedulerLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_name: str = Field(min_length=1, max_length=160)
    owner_instance_id: str = Field(min_length=1, max_length=120)
    fencing_token: int = Field(ge=1)
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime
    release_sha: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)

    def is_live(self, *, now: datetime) -> bool:
        return self.expires_at > now

    def can_start_financial_cycle(self, *, now: datetime) -> bool:
        remaining = (self.expires_at - now).total_seconds()
        return remaining >= MINIMUM_FINANCIAL_CYCLE_LEASE_SECONDS


class RuntimeControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str = Field(min_length=1, max_length=120)
    scope_type: RuntimeScopeType
    scope_id: str = Field(min_length=1, max_length=160)
    state: RuntimeControlState
    reason: str = Field(min_length=1, max_length=500)
    actor: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(min_length=1, max_length=80)
    effective_at: datetime
    recovery_condition: str | None = Field(default=None, max_length=500)
    version: int = Field(ge=1)

    @property
    def allows_new_entries(self) -> bool:
        return self.state is RuntimeControlState.RUNNING

    @property
    def allows_closing_submissions(self) -> bool:
        return self.state in {
            RuntimeControlState.RUNNING,
            RuntimeControlState.CLOSING_ONLY,
        }


class AgentCycle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cycle_id: str = Field(min_length=1, max_length=120)
    lease_name: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=120)
    account_id: str = Field(min_length=1, max_length=120)
    agent_id: str = Field(min_length=1, max_length=120)
    owner_instance_id: str = Field(min_length=1, max_length=120)
    fencing_token: int = Field(ge=1)
    release_sha: str = Field(min_length=1, max_length=80)
    started_at: datetime
    finished_at: datetime | None = None
    state: AgentCycleState = AgentCycleState.STARTED
    market_session_state: str = Field(min_length=1, max_length=40)
    lifecycle_state: str | None = Field(default=None, max_length=80)
    entry_state: str | None = Field(default=None, max_length=80)
    safe_error_type: str | None = Field(default=None, max_length=120)
    next_scheduled_at: datetime | None = None


class RuntimeControlDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control_state: RuntimeControlState
    public_reasons: tuple[str, ...]
    allow_new_entries: bool
    allow_closing_submissions: bool
    control_id: str | None = None


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lease_name: str
    lease_owner_instance_id: str | None
    fencing_token: int | None
    lease_expires_at: datetime | None
    last_heartbeat_at: datetime | None
    last_cycle_state: str | None
    effective_control_state: RuntimeControlState
