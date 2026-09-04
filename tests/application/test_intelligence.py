from decimal import Decimal
from pathlib import Path

from convictionos.application.intelligence import build_trade_intent, load_case


def test_fixture_preserves_point_in_time_provenance() -> None:
    case = load_case(Path("fixtures/synthetic_catalyst.json"))

    assert case.synthetic is True
    assert case.evidence[0].event_at <= case.evidence[0].observed_at
    assert case.evidence[0].source_checksum.startswith("sha256:")


def test_fixture_builds_defined_risk_spread() -> None:
    case = load_case(Path("fixtures/synthetic_catalyst.json"))

    intent = build_trade_intent(
        case,
        account_id="paper-account",
        mandate_id="mandate-001",
        mandate_version=1,
        long_option_symbol="SPY280120C00500000",
        short_option_symbol="SPY280120C00510000",
        limit_price=Decimal("1.25"),
    )

    assert len(intent.legs) == 2
    assert intent.max_loss == Decimal("125.00")
    assert intent.data_snapshot_hash == case.snapshot_hash()
