from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.domain.competition import COMPETITION_MANDATE_VERSION
from convictionos.domain.canonical import sha256_hex
from convictionos.domain.eligibility import (
    EligibilityManifest,
    EligibilityOutcome,
    evaluate_manifest_outcome,
)
from convictionos.domain.market_data import (
    ExecutionTier,
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.infrastructure.models import EligibilityManifestRow
from convictionos.infrastructure.store import Store


def store(engine: AsyncEngine) -> Store:
    return Store(async_sessionmaker(engine, expire_on_commit=False))


def make_manifest(
    manifest_id: str = "eligibility-001",
    *,
    workspace_id: str = "workspace-001",
    account_id: str = "paper-account",
    verified_at: datetime = datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
    outcome: EligibilityOutcome = EligibilityOutcome.ELIGIBLE,
    baseline_manifest_id: str | None = None,
) -> EligibilityManifest:
    capability = build_market_data_capability(UnderlyingFeed.IEX, OptionsFeed.OPRA, verified_at)
    computed_outcome, check_results = evaluate_manifest_outcome(
        expected_account_id=account_id,
        observed_account_id=account_id,
        environment="paper",
        account_status="ACTIVE",
        starting_equity=Decimal("100000.00"),
        starting_cash=Decimal("100000.00"),
        no_positions_at_baseline=True,
        no_orders_at_baseline=True,
        options_level=3,
        trading_api_read_ok=True,
        cli_revision="53606273aa230a40c64b783425dcb3f4423ede30",
        cli_digest="sha256:alpaca-cli",
        mcp_schema_hash="sha256:alpaca-mcp-schema",
        market_data_capability=capability,
        mandate_version=COMPETITION_MANDATE_VERSION,
        baseline_manifest_id=baseline_manifest_id,
    )
    assert computed_outcome is outcome
    return EligibilityManifest(
        manifest_id=manifest_id,
        workspace_id=workspace_id,
        expected_account_id=account_id,
        observed_account_id=account_id,
        environment="paper",
        account_status="ACTIVE",
        starting_equity=Decimal("100000.00"),
        starting_cash=Decimal("100000.00"),
        no_positions_at_baseline=True,
        no_orders_at_baseline=True,
        options_level=3,
        trading_api_read_ok=True,
        cli_version="alpaca-cli-v1",
        cli_revision="53606273aa230a40c64b783425dcb3f4423ede30",
        cli_digest="sha256:alpaca-cli",
        mcp_version="2.3.1",
        mcp_schema_hash="sha256:alpaca-mcp-schema",
        option_feed=OptionsFeed.OPRA,
        market_data_capability=capability,
        mandate_version=COMPETITION_MANDATE_VERSION,
        deployed_git_sha="f" * 40,
        verified_at=verified_at,
        check_results=check_results,
        outcome=outcome,
        baseline_manifest_id=baseline_manifest_id,
    )


@pytest.mark.asyncio
async def test_create_or_load_manifest_is_idempotent(engine: AsyncEngine) -> None:
    manifest = make_manifest()

    first = await store(engine).create_or_load_eligibility_manifest(manifest)
    second = await store(engine).create_or_load_eligibility_manifest(manifest)

    assert first == second == manifest


@pytest.mark.asyncio
async def test_same_manifest_id_cannot_change_payload(engine: AsyncEngine) -> None:
    manifest = make_manifest()
    await store(engine).create_or_load_eligibility_manifest(manifest)
    changed = manifest.model_copy(update={"account_status": "INACTIVE"})

    with pytest.raises(ValueError, match="different manifest hash"):
        await store(engine).create_or_load_eligibility_manifest(changed)


@pytest.mark.asyncio
async def test_load_latest_manifest_is_scoped_by_workspace_and_account(
    engine: AsyncEngine,
) -> None:
    first = make_manifest("eligibility-001")
    second = make_manifest(
        "eligibility-002",
        verified_at=datetime(2026, 9, 4, 14, 10, tzinfo=UTC),
        baseline_manifest_id=first.manifest_id,
    )
    other = make_manifest(
        "eligibility-other",
        workspace_id="workspace-002",
        verified_at=datetime(2026, 9, 4, 14, 15, tzinfo=UTC),
    )
    await store(engine).create_or_load_eligibility_manifest(first)
    await store(engine).create_or_load_eligibility_manifest(second)
    await store(engine).create_or_load_eligibility_manifest(other)

    latest = await store(engine).latest_eligibility_manifest(
        workspace_id="workspace-001", account_id="paper-account"
    )

    assert latest == second


@pytest.mark.asyncio
async def test_latest_baseline_skips_linked_current_manifests(engine: AsyncEngine) -> None:
    baseline = make_manifest("eligibility-baseline")
    current = make_manifest(
        "eligibility-current",
        verified_at=datetime(2026, 9, 4, 14, 5, tzinfo=UTC),
        baseline_manifest_id=baseline.manifest_id,
    )
    await store(engine).create_or_load_eligibility_manifest(baseline)
    await store(engine).create_or_load_eligibility_manifest(current)

    assert await store(engine).latest_baseline("workspace-001", "paper-account") == baseline


@pytest.mark.asyncio
async def test_manifest_hash_conflict_is_rejected(engine: AsyncEngine) -> None:
    manifest = make_manifest()
    await store(engine).create_or_load_eligibility_manifest(manifest)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(
            text(
                "UPDATE eligibility_manifests SET manifest_hash = :hash "
                "WHERE manifest_id = :manifest_id"
            ),
            {"hash": "0" * 64, "manifest_id": manifest.manifest_id},
        )
        await session.commit()

    with pytest.raises(ValueError, match="stored manifest hash mismatch"):
        await store(engine).latest_eligibility_manifest("workspace-001", "paper-account")


@pytest.mark.asyncio
async def test_legacy_indicative_manifest_loads_with_current_paper_eligibility(
    engine: AsyncEngine,
) -> None:
    legacy_payload = make_manifest(
        "eligibility-legacy-indicative"
    ).model_dump(mode="json")
    capability = build_market_data_capability(
        UnderlyingFeed.IEX,
        OptionsFeed.INDICATIVE,
        datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
    )
    legacy_payload["option_feed"] = "indicative"
    legacy_payload["market_data_capability"] = capability.model_dump(mode="json")
    legacy_payload["outcome"] = "ineligible"
    legacy_payload["check_results"] = [
        {
            **check,
            "status": "fail",
            "public_reason": "OPRA feed is required for performance evidence",
        }
        if check["name"] == "performance_feed"
        else check
        for check in legacy_payload["check_results"]
    ]
    assert legacy_payload["market_data_capability"]["execution_tier"] == ExecutionTier.PAPER_EXECUTABLE

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            EligibilityManifestRow(
                manifest_id=legacy_payload["manifest_id"],
                manifest_hash=sha256_hex(legacy_payload),
                workspace_id=legacy_payload["workspace_id"],
                account_id=legacy_payload["observed_account_id"],
                outcome=legacy_payload["outcome"],
                verified_at=datetime.fromisoformat(legacy_payload["verified_at"]),
                payload=legacy_payload,
                baseline_manifest_id=None,
            )
        )
        await session.commit()

    latest = await store(engine).latest_baseline("workspace-001", "paper-account")

    assert latest is not None
    assert latest.outcome is EligibilityOutcome.ELIGIBLE
    assert latest.option_feed is OptionsFeed.INDICATIVE
    assert latest.operational_paper_ready
    assert not latest.performance_evidence_ready
