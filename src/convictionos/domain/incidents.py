from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BreakerKind(StrEnum):
    DAILY_LOSS = "daily_loss"
    MARKET_DATA = "market_data"
    AI_MCP_DEGRADATION = "ai_mcp_degradation"
    BROKER_RECONCILIATION = "broker_reconciliation"
    STRATEGY_DRIFT = "strategy_drift"
    ASSIGNMENT_EXPIRATION = "assignment_expiration"
    COMPETITION_DEADLINE = "competition_deadline"
    DATABASE_RUNTIME = "database_runtime"
    MANUAL_PAUSE = "manual_pause"


class IncidentSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IncidentState(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class RuntimeIncident(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: str = Field(min_length=1, max_length=120)
    public_id: str = Field(min_length=1, max_length=80)
    workspace_id: str = Field(min_length=1, max_length=120)
    account_id: str = Field(min_length=1, max_length=120)
    agent_id: str = Field(min_length=1, max_length=120)
    kind: BreakerKind
    severity: IncidentSeverity
    state: IncidentState
    trigger_evidence_hash: str = Field(min_length=64, max_length=64)
    policy_version: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)
    recovery_condition: str = Field(min_length=1, max_length=500)
    opened_at: datetime
    acknowledged_by: str | None = Field(default=None, max_length=120)
    resolved_by: str | None = Field(default=None, max_length=120)
    recovery_evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)
    version: int = Field(ge=1)


class IncidentEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=120)
    incident_id: str = Field(min_length=1, max_length=120)
    actor: str = Field(min_length=1, max_length=120)
    event_type: str = Field(min_length=1, max_length=80)
    evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)
    observed_at: datetime
