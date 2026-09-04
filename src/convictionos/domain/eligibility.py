from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.competition import COMPETITION_MANDATE_VERSION
from convictionos.domain.market_data import (
    ExecutionTier,
    MarketDataCapability,
    OptionsFeed,
)


class EligibilityOutcome(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNAVAILABLE = "unavailable"


class EligibilityCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class EligibilityCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    status: EligibilityCheckStatus
    public_reason: str = Field(min_length=1, max_length=500)


class EligibilityManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str = Field(min_length=1, max_length=120)
    workspace_id: str = Field(min_length=1, max_length=120)
    expected_account_id: str
    observed_account_id: str
    environment: str
    account_status: str
    starting_equity: Decimal
    starting_cash: Decimal
    no_positions_at_baseline: bool
    no_orders_at_baseline: bool
    options_level: int
    trading_api_read_ok: bool
    cli_version: str
    cli_revision: str
    cli_digest: str
    mcp_version: str
    mcp_schema_hash: str
    option_feed: OptionsFeed
    market_data_capability: MarketDataCapability
    mandate_version: str
    deployed_git_sha: str
    verified_at: datetime
    check_results: tuple[EligibilityCheckResult, ...]
    outcome: EligibilityOutcome
    baseline_manifest_id: str | None = None

    @model_validator(mode="after")
    def validate_manifest(self) -> "EligibilityManifest":
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")
        if self.option_feed is not self.market_data_capability.options_feed:
            raise ValueError("option feed must match market data capability")
        expected_outcome, expected_checks = evaluate_manifest_outcome(
            expected_account_id=self.expected_account_id,
            observed_account_id=self.observed_account_id,
            environment=self.environment,
            account_status=self.account_status,
            starting_equity=self.starting_equity,
            starting_cash=self.starting_cash,
            no_positions_at_baseline=self.no_positions_at_baseline,
            no_orders_at_baseline=self.no_orders_at_baseline,
            options_level=self.options_level,
            trading_api_read_ok=self.trading_api_read_ok,
            cli_revision=self.cli_revision,
            cli_digest=self.cli_digest,
            mcp_schema_hash=self.mcp_schema_hash,
            market_data_capability=self.market_data_capability,
            mandate_version=self.mandate_version,
            baseline_manifest_id=self.baseline_manifest_id,
        )
        if self.outcome is not expected_outcome or self.check_results != expected_checks:
            raise ValueError("eligibility outcome and check results must match payload")
        return self

    @property
    def public_reasons(self) -> tuple[str, ...]:
        _, checks = evaluate_manifest_outcome(
            expected_account_id=self.expected_account_id,
            observed_account_id=self.observed_account_id,
            environment=self.environment,
            account_status=self.account_status,
            starting_equity=self.starting_equity,
            starting_cash=self.starting_cash,
            no_positions_at_baseline=self.no_positions_at_baseline,
            no_orders_at_baseline=self.no_orders_at_baseline,
            options_level=self.options_level,
            trading_api_read_ok=self.trading_api_read_ok,
            cli_revision=self.cli_revision,
            cli_digest=self.cli_digest,
            mcp_schema_hash=self.mcp_schema_hash,
            market_data_capability=self.market_data_capability,
            mandate_version=self.mandate_version,
            baseline_manifest_id=self.baseline_manifest_id,
        )
        reasons = [
            result.public_reason
            for result in checks
            if result.status is not EligibilityCheckStatus.PASS
        ]
        return tuple(reasons)

    @property
    def operational_paper_ready(self) -> bool:
        _, checks = evaluate_manifest_outcome(
            expected_account_id=self.expected_account_id,
            observed_account_id=self.observed_account_id,
            environment=self.environment,
            account_status=self.account_status,
            starting_equity=self.starting_equity,
            starting_cash=self.starting_cash,
            no_positions_at_baseline=self.no_positions_at_baseline,
            no_orders_at_baseline=self.no_orders_at_baseline,
            options_level=self.options_level,
            trading_api_read_ok=self.trading_api_read_ok,
            cli_revision=self.cli_revision,
            cli_digest=self.cli_digest,
            mcp_schema_hash=self.mcp_schema_hash,
            market_data_capability=self.market_data_capability,
            mandate_version=self.mandate_version,
            baseline_manifest_id=self.baseline_manifest_id,
        )
        return not any(
            result.status is not EligibilityCheckStatus.PASS
            and result.name != "performance_feed"
            for result in checks
        )

    @property
    def performance_evidence_ready(self) -> bool:
        return (
            self.outcome is EligibilityOutcome.ELIGIBLE
            and self.market_data_capability.options_feed is OptionsFeed.OPRA
            and self.market_data_capability.execution_tier is ExecutionTier.PAPER_EXECUTABLE
        )

    def is_fresh_at(self, now: datetime) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return now - self.verified_at <= timedelta(minutes=15)

    def manifest_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json", exclude_none=True))


def evaluate_manifest_outcome(
    *,
    expected_account_id: str,
    observed_account_id: str,
    environment: str,
    account_status: str,
    starting_equity: Decimal,
    starting_cash: Decimal,
    no_positions_at_baseline: bool,
    no_orders_at_baseline: bool,
    options_level: int,
    trading_api_read_ok: bool,
    cli_revision: str,
    cli_digest: str,
    mcp_schema_hash: str,
    market_data_capability: MarketDataCapability,
    mandate_version: str,
    baseline_manifest_id: str | None,
) -> tuple[EligibilityOutcome, tuple[EligibilityCheckResult, ...]]:
    checks = (
        _check(
            "account_identity",
            bool(expected_account_id.strip()) and observed_account_id == expected_account_id,
            "paper account matched",
            "missing expected account" if not expected_account_id.strip() else "account mismatch",
        ),
        _check(
            "environment",
            environment == "paper",
            "paper environment confirmed",
            "paper environment required",
        ),
        _check(
            "account_status",
            account_status == "ACTIVE",
            "ACTIVE account confirmed",
            "ACTIVE account required",
        ),
        _check(
            "starting_equity",
            starting_equity == Decimal("100000.00"),
            "100000.00 equity confirmed",
            "100000.00 equity required"
            if baseline_manifest_id is None
            else "baseline already captured; current equity differs from fresh baseline",
        ),
        _check(
            "starting_cash",
            starting_cash == Decimal("100000.00"),
            "100000.00 cash confirmed",
            "100000.00 cash required"
            if baseline_manifest_id is None
            else "baseline already captured; current cash differs from fresh baseline",
        ),
        _check(
            "no_positions_at_baseline",
            no_positions_at_baseline or baseline_manifest_id is not None,
            "no positions at baseline",
            "positions at baseline are not allowed",
        ),
        _check(
            "no_orders_at_baseline",
            no_orders_at_baseline or baseline_manifest_id is not None,
            "no orders at baseline",
            "orders at baseline are not allowed",
        ),
        _check(
            "options_level",
            options_level >= 3,
            "options level 3 confirmed",
            "options level 3 required for MLEG vertical spreads",
        ),
        _check(
            "trading_api_read",
            trading_api_read_ok,
            "Trading API read succeeded",
            "Trading API read check failed",
        ),
        _check(
            "cli_pin",
            bool(cli_revision.strip()),
            "CLI pin confirmed",
            "CLI pin is required",
            unavailable=True,
        ),
        _check(
            "cli_digest",
            bool(cli_digest.strip()),
            "CLI digest confirmed",
            "CLI digest is required",
            unavailable=True,
        ),
        _check(
            "mcp_schema",
            bool(mcp_schema_hash.strip()),
            "MCP schema confirmed",
            "MCP schema is required",
            unavailable=True,
        ),
        _check(
            "performance_feed",
            market_data_capability.execution_tier is ExecutionTier.PAPER_EXECUTABLE,
            "options feed confirmed for paper execution",
            "paper-executable options feed is required",
        ),
        _check(
            "mandate_version",
            mandate_version == COMPETITION_MANDATE_VERSION,
            "competition mandate confirmed",
            "competition mandate version mismatch",
        ),
    )
    if any(result.status is EligibilityCheckStatus.UNAVAILABLE for result in checks):
        return EligibilityOutcome.UNAVAILABLE, checks
    if any(result.status is EligibilityCheckStatus.FAIL for result in checks):
        return EligibilityOutcome.INELIGIBLE, checks
    return EligibilityOutcome.ELIGIBLE, checks


def _check(
    name: str,
    passed: bool,
    pass_reason: str,
    fail_reason: str,
    *,
    unavailable: bool = False,
) -> EligibilityCheckResult:
    if passed:
        return EligibilityCheckResult(
            name=name,
            status=EligibilityCheckStatus.PASS,
            public_reason=pass_reason,
        )
    return EligibilityCheckResult(
        name=name,
        status=EligibilityCheckStatus.UNAVAILABLE if unavailable else EligibilityCheckStatus.FAIL,
        public_reason=fail_reason,
    )
