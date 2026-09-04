from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from convictionos.application.eligibility import (
    AccountEligibilitySnapshot,
    CliEligibilitySnapshot,
    EligibilityBlocked,
    EligibilityService,
    McpEligibilitySnapshot,
)
from convictionos.domain.eligibility import EligibilityManifest, EligibilityOutcome
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)

NOW = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)


@dataclass
class FakeAccountPort:
    snapshot: AccountEligibilitySnapshot

    async def inspect(self) -> AccountEligibilitySnapshot:
        return self.snapshot


@dataclass
class FakeCliPort:
    snapshot: CliEligibilitySnapshot

    async def inspect(self) -> CliEligibilitySnapshot:
        return self.snapshot


@dataclass
class FakeMcpPort:
    snapshot: McpEligibilitySnapshot

    async def inspect(self) -> McpEligibilitySnapshot:
        return self.snapshot


@dataclass
class FakeDataPort:
    options_feed: OptionsFeed = OptionsFeed.OPRA

    async def capability(self, now: datetime):
        return build_market_data_capability(UnderlyingFeed.IEX, self.options_feed, now)


class MemoryManifestStore:
    def __init__(self) -> None:
        self.saved: list[EligibilityManifest] = []

    async def latest_baseline(
        self, workspace_id: str, account_id: str
    ) -> EligibilityManifest | None:
        for manifest in reversed(self.saved):
            if (
                manifest.workspace_id == workspace_id
                and manifest.observed_account_id == account_id
                and manifest.baseline_manifest_id is None
            ):
                return manifest
        return None

    async def create_or_load_eligibility_manifest(
        self, manifest: EligibilityManifest
    ) -> EligibilityManifest:
        self.saved.append(manifest)
        return manifest


def account_snapshot(**overrides: object) -> AccountEligibilitySnapshot:
    values = {
        "account_id": "paper-account",
        "environment": "paper",
        "status": "ACTIVE",
        "equity": Decimal("100000.00"),
        "cash": Decimal("100000.00"),
        "open_position_count": 0,
        "open_order_count": 0,
        "options_level": 3,
        "trading_api_read_ok": True,
    }
    values.update(overrides)
    return AccountEligibilitySnapshot(**values)


def service(
    *,
    account: AccountEligibilitySnapshot | None = None,
    cli_revision: str = "53606273aa230a40c64b783425dcb3f4423ede30",
    cli_digest: str = "sha256:alpaca-cli",
    mcp_schema_hash: str = "sha256:alpaca-mcp-schema",
    options_feed: OptionsFeed = OptionsFeed.OPRA,
    store: MemoryManifestStore | None = None,
) -> EligibilityService:
    return EligibilityService(
        account_port=FakeAccountPort(account or account_snapshot()),
        cli_port=FakeCliPort(
            CliEligibilitySnapshot(
                version="alpaca-cli-v1",
                revision=cli_revision,
                digest=cli_digest,
            )
        ),
        mcp_port=FakeMcpPort(
            McpEligibilitySnapshot(version="2.3.1", schema_hash=mcp_schema_hash)
        ),
        data_port=FakeDataPort(options_feed),
        manifest_store=store or MemoryManifestStore(),
        deployed_git_sha="f" * 40,
    )


@pytest.mark.asyncio
async def test_verify_returns_eligible_manifest_for_clean_fresh_paper_account() -> None:
    result = await service().verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=True,
        now=NOW,
    )

    assert result.outcome is EligibilityOutcome.ELIGIBLE
    assert result.operational_paper_ready
    assert result.performance_evidence_ready
    assert result.baseline_manifest_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("account", "expected_reason"),
    [
        (account_snapshot(account_id="other-account"), "account mismatch"),
        (account_snapshot(environment="live"), "paper environment"),
        (account_snapshot(status="INACTIVE"), "ACTIVE account"),
        (account_snapshot(equity=Decimal("99999.99")), "100000.00 equity"),
        (account_snapshot(cash=Decimal("99999.99")), "100000.00 cash"),
        (account_snapshot(open_position_count=1), "positions at baseline"),
        (account_snapshot(open_order_count=1), "orders at baseline"),
        (account_snapshot(options_level=2), "options level 3"),
        (account_snapshot(trading_api_read_ok=False), "Trading API read"),
    ],
)
async def test_verify_marks_required_account_failures_ineligible(
    account: AccountEligibilitySnapshot, expected_reason: str
) -> None:
    result = await service(account=account).verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=True,
        now=NOW,
    )

    assert result.outcome is EligibilityOutcome.INELIGIBLE
    assert expected_reason in " ".join(result.public_reasons)


@pytest.mark.asyncio
async def test_verify_marks_missing_toolchain_or_mcp_pins_unavailable() -> None:
    result = await service(cli_revision="", mcp_schema_hash="").verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=True,
        now=NOW,
    )

    assert result.outcome is EligibilityOutcome.UNAVAILABLE
    assert "CLI pin" in " ".join(result.public_reasons)
    assert "MCP schema" in " ".join(result.public_reasons)


@pytest.mark.asyncio
async def test_indicative_feed_qualifies_hackathon_paper_readiness() -> None:
    result = await service(options_feed=OptionsFeed.INDICATIVE).verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=True,
        now=NOW,
    )

    assert result.outcome is EligibilityOutcome.ELIGIBLE
    assert result.operational_paper_ready
    assert not result.performance_evidence_ready


@pytest.mark.asyncio
async def test_guard_allows_indicative_feed_for_hackathon_paper() -> None:
    result = await service(options_feed=OptionsFeed.INDICATIVE).verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=True,
        now=NOW,
    )

    result.raise_if_not_eligible()


@pytest.mark.asyncio
async def test_later_equity_change_links_current_manifest_to_append_only_baseline() -> None:
    store = MemoryManifestStore()
    baseline = await service(store=store).verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=True,
        now=NOW,
    )

    current = await service(
        account=account_snapshot(equity=Decimal("100250.00"), cash=Decimal("100250.00")),
        store=store,
    ).verify(
        workspace_id="workspace-001",
        expected_account_id="paper-account",
        capture_baseline=False,
        now=NOW + timedelta(minutes=5),
    )

    assert current.baseline_manifest_id == baseline.manifest_id
    assert current.outcome is EligibilityOutcome.INELIGIBLE
    assert "baseline already captured" in " ".join(current.public_reasons)
