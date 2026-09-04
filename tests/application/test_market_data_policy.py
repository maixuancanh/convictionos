from datetime import UTC, datetime
from decimal import Decimal

from convictionos.application.market_data_policy import (
    evaluate_market_data_policy,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from tests.domain.test_trade_intent import make_intent

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def capability(options_feed: OptionsFeed):
    return build_market_data_capability(UnderlyingFeed.IEX, options_feed, NOW)


def test_indicative_exact_one_unit_is_allowed() -> None:
    decision = evaluate_market_data_policy(capability(OptionsFeed.INDICATIVE), make_intent())

    assert decision.allowed is True
    assert decision.reasons == ()


def test_indicative_more_than_one_unit_is_denied() -> None:
    intent = make_intent().model_copy(
        update={
            "legs": tuple(
                leg.model_copy(update={"quantity": Decimal("2")})
                for leg in make_intent().legs
            )
        }
    )

    decision = evaluate_market_data_policy(capability(OptionsFeed.INDICATIVE), intent)

    assert decision.allowed is False
    assert decision.reasons == ("market_data_spread_unit_ceiling_exceeded",)


def test_fractional_quantity_is_denied() -> None:
    base = make_intent()
    intent = base.model_copy(
        update={
            "legs": tuple(
                leg.model_copy(update={"quantity": Decimal("1.5")}) for leg in base.legs
            )
        }
    )

    decision = evaluate_market_data_policy(capability(OptionsFeed.OPRA), intent)

    assert decision.allowed is False
    assert decision.reasons == ("market_data_invalid_spread_unit_quantity",)


def test_research_only_is_denied() -> None:
    decision = evaluate_market_data_policy(capability(OptionsFeed.UNKNOWN), make_intent())

    assert decision.allowed is False
    assert decision.reasons == ("market_data_research_only",)
