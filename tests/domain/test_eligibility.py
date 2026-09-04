from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.domain.competition import COMPETITION_MANDATE_VERSION
from convictionos.domain.eligibility import (
    EligibilityCheckResult,
    EligibilityCheckStatus,
    EligibilityManifest,
    EligibilityOutcome,
    evaluate_manifest_outcome,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)


def make_manifest(
    *,
    manifest_id: str = "eligibility-001",
    outcome: EligibilityOutcome = EligibilityOutcome.ELIGIBLE,
    expected_account_id: str = "paper-account",
    observed_account_id: str = "paper-account",
    environment: str = "paper",
    account_status: str = "ACTIVE",
    starting_equity: Decimal = Decimal("100000.00"),
    starting_cash: Decimal = Decimal("100000.00"),
    no_positions_at_baseline: bool = True,
    no_orders_at_baseline: bool = True,
    options_level: int = 3,
    trading_api_read_ok: bool = True,
    cli_revision: str = "53606273aa230a40c64b783425dcb3f4423ede30",
    cli_digest: str = "sha256:alpaca-cli",
    mcp_schema_hash: str = "sha256:alpaca-mcp-schema",
    options_feed: OptionsFeed = OptionsFeed.OPRA,
    verified_at: datetime = datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
    baseline_manifest_id: str | None = None,
) -> EligibilityManifest:
    capability = build_market_data_capability(
        UnderlyingFeed.IEX, options_feed, verified_at
    )
    computed_outcome, check_results = evaluate_manifest_outcome(
        expected_account_id=expected_account_id,
        observed_account_id=observed_account_id,
        environment=environment,
        account_status=account_status,
        starting_equity=starting_equity,
        starting_cash=starting_cash,
        no_positions_at_baseline=no_positions_at_baseline,
        no_orders_at_baseline=no_orders_at_baseline,
        options_level=options_level,
        trading_api_read_ok=trading_api_read_ok,
        cli_revision=cli_revision,
        cli_digest=cli_digest,
        mcp_schema_hash=mcp_schema_hash,
        market_data_capability=capability,
        mandate_version=COMPETITION_MANDATE_VERSION,
        baseline_manifest_id=baseline_manifest_id,
    )
    assert computed_outcome is outcome
    return EligibilityManifest(
        manifest_id=manifest_id,
        workspace_id="workspace-001",
        expected_account_id=expected_account_id,
        observed_account_id=observed_account_id,
        environment=environment,
        account_status=account_status,
        starting_equity=starting_equity,
        starting_cash=starting_cash,
        no_positions_at_baseline=no_positions_at_baseline,
        no_orders_at_baseline=no_orders_at_baseline,
        options_level=options_level,
        trading_api_read_ok=trading_api_read_ok,
        cli_version="alpaca-cli-v1",
        cli_revision=cli_revision,
        cli_digest=cli_digest,
        mcp_version="2.3.1",
        mcp_schema_hash=mcp_schema_hash,
        option_feed=options_feed,
        market_data_capability=capability,
        mandate_version=COMPETITION_MANDATE_VERSION,
        deployed_git_sha="f" * 40,
        verified_at=verified_at,
        check_results=check_results,
        outcome=outcome,
        baseline_manifest_id=baseline_manifest_id,
    )


def test_manifest_is_immutable_and_hashes_canonical_payload() -> None:
    manifest = make_manifest()

    with pytest.raises(ValidationError):
        manifest.account_status = "INACTIVE"  # type: ignore[misc]

    assert manifest.manifest_hash() == manifest.manifest_hash()
    changed = manifest.model_copy(update={"account_status": "INACTIVE"})
    assert changed.manifest_hash() != manifest.manifest_hash()


@pytest.mark.parametrize(
    ("updates", "expected_outcome", "reason"),
    [
        ({"expected_account_id": ""}, EligibilityOutcome.INELIGIBLE, "missing expected account"),
        (
            {"observed_account_id": "other-account"},
            EligibilityOutcome.INELIGIBLE,
            "account mismatch",
        ),
        ({"environment": "live"}, EligibilityOutcome.INELIGIBLE, "paper environment"),
        ({"account_status": "INACTIVE"}, EligibilityOutcome.INELIGIBLE, "ACTIVE account"),
        (
            {"starting_equity": Decimal("99999.99")},
            EligibilityOutcome.INELIGIBLE,
            "100000.00 equity",
        ),
        ({"starting_cash": Decimal("99999.99")}, EligibilityOutcome.INELIGIBLE, "100000.00 cash"),
        (
            {"no_positions_at_baseline": False},
            EligibilityOutcome.INELIGIBLE,
            "positions at baseline",
        ),
        ({"no_orders_at_baseline": False}, EligibilityOutcome.INELIGIBLE, "orders at baseline"),
        ({"options_level": 2}, EligibilityOutcome.INELIGIBLE, "options level 3"),
        ({"trading_api_read_ok": False}, EligibilityOutcome.INELIGIBLE, "Trading API read"),
        ({"cli_revision": ""}, EligibilityOutcome.UNAVAILABLE, "CLI pin"),
        ({"cli_digest": ""}, EligibilityOutcome.UNAVAILABLE, "CLI digest"),
        ({"mcp_schema_hash": ""}, EligibilityOutcome.UNAVAILABLE, "MCP schema"),
    ],
)
def test_manifest_evaluates_ineligible_for_mismatches(
    updates: dict[str, object], expected_outcome: EligibilityOutcome, reason: str
) -> None:
    manifest = make_manifest(**updates, outcome=expected_outcome)

    assert manifest.outcome is expected_outcome
    assert reason in " ".join(manifest.public_reasons)
    assert not manifest.operational_paper_ready
    assert not manifest.performance_evidence_ready


def test_indicative_feed_qualifies_hackathon_paper_without_opra_evidence() -> None:
    manifest = make_manifest(
        options_feed=OptionsFeed.INDICATIVE,
        outcome=EligibilityOutcome.ELIGIBLE,
    )

    assert manifest.outcome is EligibilityOutcome.ELIGIBLE
    assert manifest.operational_paper_ready
    assert not manifest.performance_evidence_ready
    assert not manifest.public_reasons


def test_stale_manifest_prevents_funded_scheduling() -> None:
    manifest = make_manifest(verified_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC))

    assert manifest.is_fresh_at(
        datetime(2026, 9, 4, 14, 14, 59, tzinfo=UTC)
    )
    assert not manifest.is_fresh_at(
        datetime(2026, 9, 4, 14, 15, 1, tzinfo=UTC)
    )


def test_manifest_requires_timezone_aware_verification_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_manifest(verified_at=datetime(2026, 9, 4, 14, 0))


def test_manifest_rejects_inconsistent_check_results() -> None:
    with pytest.raises(ValidationError, match="check results"):
        EligibilityManifest(
            **{
                **make_manifest().model_dump(mode="python"),
                "check_results": (
                    EligibilityCheckResult(
                        name="account_identity",
                        status=EligibilityCheckStatus.FAIL,
                        public_reason="fake failure",
                    ),
                ),
            }
        )


def test_current_capability_manifest_links_to_historical_baseline() -> None:
    baseline = make_manifest(manifest_id="eligibility-baseline")
    current = make_manifest(
        manifest_id="eligibility-current",
        starting_equity=Decimal("100250.00"),
        starting_cash=Decimal("100250.00"),
        outcome=EligibilityOutcome.INELIGIBLE,
        baseline_manifest_id=baseline.manifest_id,
    )

    assert current.baseline_manifest_id == "eligibility-baseline"
    assert "baseline already captured" in " ".join(current.public_reasons)
