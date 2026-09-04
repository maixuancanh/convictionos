from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from convictionos.domain.competition import COMPETITION_MANDATE_VERSION
from convictionos.domain.eligibility import (
    EligibilityManifest,
    EligibilityOutcome,
    evaluate_manifest_outcome,
)
from convictionos.domain.market_data import MarketDataCapability


@dataclass(frozen=True)
class AccountEligibilitySnapshot:
    account_id: str
    environment: str
    status: str
    equity: Decimal
    cash: Decimal
    open_position_count: int
    open_order_count: int
    options_level: int
    trading_api_read_ok: bool


@dataclass(frozen=True)
class CliEligibilitySnapshot:
    version: str
    revision: str
    digest: str


@dataclass(frozen=True)
class McpEligibilitySnapshot:
    version: str
    schema_hash: str


class AccountEligibilityPort(Protocol):
    async def inspect(self) -> AccountEligibilitySnapshot: ...


class CliEligibilityPort(Protocol):
    async def inspect(self) -> CliEligibilitySnapshot: ...


class McpEligibilityPort(Protocol):
    async def inspect(self) -> McpEligibilitySnapshot: ...


class MarketDataEligibilityPort(Protocol):
    async def capability(self, now: datetime) -> MarketDataCapability: ...


class EligibilityManifestStore(Protocol):
    async def latest_baseline(
        self, workspace_id: str, account_id: str
    ) -> EligibilityManifest | None: ...

    async def create_or_load_eligibility_manifest(
        self, manifest: EligibilityManifest
    ) -> EligibilityManifest: ...


class EligibilityBlocked(RuntimeError):
    def __init__(self, public_reasons: tuple[str, ...]) -> None:
        self.public_reasons = public_reasons
        super().__init__("; ".join(public_reasons))


class EligibilityResult(EligibilityManifest):
    def raise_if_not_eligible(self) -> None:
        if self.outcome is not EligibilityOutcome.ELIGIBLE:
            raise EligibilityBlocked(self.public_reasons)


class EligibilityService:
    def __init__(
        self,
        *,
        account_port: AccountEligibilityPort,
        cli_port: CliEligibilityPort,
        mcp_port: McpEligibilityPort,
        data_port: MarketDataEligibilityPort,
        manifest_store: EligibilityManifestStore,
        deployed_git_sha: str,
    ) -> None:
        self._account_port = account_port
        self._cli_port = cli_port
        self._mcp_port = mcp_port
        self._data_port = data_port
        self._manifest_store = manifest_store
        self._deployed_git_sha = deployed_git_sha

    async def verify(
        self,
        *,
        workspace_id: str,
        expected_account_id: str,
        capture_baseline: bool,
        now: datetime,
    ) -> EligibilityResult:
        account = await self._account_port.inspect()
        cli = await self._cli_port.inspect()
        mcp = await self._mcp_port.inspect()
        capability = await self._data_port.capability(now)
        baseline = await self._manifest_store.latest_baseline(
            workspace_id, account.account_id
        )
        baseline_manifest_id = None
        if not capture_baseline and baseline is not None:
            baseline_manifest_id = baseline.manifest_id
        no_positions = account.open_position_count == 0
        no_orders = account.open_order_count == 0
        outcome, checks = evaluate_manifest_outcome(
            expected_account_id=expected_account_id,
            observed_account_id=account.account_id,
            environment=account.environment,
            account_status=account.status,
            starting_equity=account.equity,
            starting_cash=account.cash,
            no_positions_at_baseline=no_positions,
            no_orders_at_baseline=no_orders,
            options_level=account.options_level,
            trading_api_read_ok=account.trading_api_read_ok,
            cli_revision=cli.revision,
            cli_digest=cli.digest,
            mcp_schema_hash=mcp.schema_hash,
            market_data_capability=capability,
            mandate_version=COMPETITION_MANDATE_VERSION,
            baseline_manifest_id=baseline_manifest_id,
        )
        manifest = EligibilityResult(
            manifest_id=_manifest_id(workspace_id, account.account_id, now),
            workspace_id=workspace_id,
            expected_account_id=expected_account_id,
            observed_account_id=account.account_id,
            environment=account.environment,
            account_status=account.status,
            starting_equity=account.equity,
            starting_cash=account.cash,
            no_positions_at_baseline=no_positions,
            no_orders_at_baseline=no_orders,
            options_level=account.options_level,
            trading_api_read_ok=account.trading_api_read_ok,
            cli_version=cli.version,
            cli_revision=cli.revision,
            cli_digest=cli.digest,
            mcp_version=mcp.version,
            mcp_schema_hash=mcp.schema_hash,
            option_feed=capability.options_feed,
            market_data_capability=capability,
            mandate_version=COMPETITION_MANDATE_VERSION,
            deployed_git_sha=self._deployed_git_sha,
            verified_at=now,
            check_results=checks,
            outcome=outcome,
            baseline_manifest_id=baseline_manifest_id,
        )
        saved = await self._manifest_store.create_or_load_eligibility_manifest(manifest)
        return EligibilityResult.model_validate(saved.model_dump(mode="python"))


def _manifest_id(workspace_id: str, account_id: str, now: datetime) -> str:
    identity = uuid5(NAMESPACE_URL, f"{workspace_id}:{account_id}:{now.isoformat()}")
    return f"eligibility-{identity.hex}"
