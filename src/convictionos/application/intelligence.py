import json
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from convictionos.domain.evidence import SyntheticNarrativeCase
from convictionos.domain.trading import (
    InstrumentKind,
    OrderType,
    PositionIntent,
    Side,
    TradeIntent,
    TradeLeg,
)


def load_case(path: Path) -> SyntheticNarrativeCase:
    return SyntheticNarrativeCase.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_synthetic_case() -> SyntheticNarrativeCase:
    packaged = files("convictionos").joinpath("fixtures/synthetic_catalyst.json")
    return SyntheticNarrativeCase.model_validate(json.loads(packaged.read_text(encoding="utf-8")))


def build_trade_intent(
    case: SyntheticNarrativeCase,
    *,
    account_id: str,
    mandate_id: str,
    mandate_version: int,
    long_option_symbol: str,
    short_option_symbol: str,
    limit_price: Decimal,
) -> TradeIntent:
    quantity = Decimal("1")
    return TradeIntent(
        intent_id=f"intent-{case.case_id}",
        idempotency_key=f"intent-{case.case_id}-v1",
        account_id=account_id,
        mandate_id=mandate_id,
        mandate_version=mandate_version,
        created_at=case.created_at,
        expires_at=case.expires_at,
        horizon=case.horizon,
        underlying=case.underlying,
        thesis_ref=case.case_id,
        legs=(
            TradeLeg(
                symbol=long_option_symbol,
                kind=InstrumentKind.US_OPTION,
                side=Side.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
                quantity=quantity,
                ratio_quantity=1,
            ),
            TradeLeg(
                symbol=short_option_symbol,
                kind=InstrumentKind.US_OPTION,
                side=Side.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
                quantity=quantity,
                ratio_quantity=1,
            ),
        ),
        order_type=OrderType.LIMIT,
        limit_price=limit_price,
        max_loss=(limit_price * Decimal("100")).quantize(Decimal("0.01")),
        exit_plan=f"Exit before expiry; invalidate when: {case.invalidation}",
        data_snapshot_hash=case.snapshot_hash(),
    )
