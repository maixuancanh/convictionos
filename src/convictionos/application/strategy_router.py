from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import combinations

from pydantic import BaseModel, ConfigDict, Field

from convictionos.application.agent import AgentMarketFrame
from convictionos.domain.canonical import sha256_hex
from convictionos.domain.intelligence import GroundedThesis, ThesisDirection
from convictionos.domain.strategy import (
    CATALYST_DTE,
    SWING_DTE,
    THEMATIC_DTE,
    OptionMarketQuote,
    StrategyCandidate,
)
from convictionos.domain.trading import Horizon

UTILITY_VERSION = "options-router-utility-v1"
_HUNDRED = Decimal("100")
_FRESHNESS = timedelta(minutes=5)


class QuantSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: ThesisDirection
    momentum: Decimal
    realized_volatility: Decimal = Field(ge=0)
    as_of: datetime
    version: str = Field(min_length=1)


class RouterOutcome(StrEnum):
    SELECTED = "selected"
    ABSTAIN = "abstain"


class RouterDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RouterOutcome
    selected: StrategyCandidate | None = None
    alternatives: tuple[StrategyCandidate, ...] = ()
    reasons: tuple[str, ...] = ()
    utility_version: str = UTILITY_VERSION


class StrategyRouter:
    def __init__(self, *, max_debit: Decimal = Decimal("2.50")) -> None:
        if max_debit <= 0:
            raise ValueError("max_debit must be positive")
        self._max_debit = max_debit

    def route(
        self,
        thesis: GroundedThesis,
        frame: AgentMarketFrame,
        quant: QuantSignal,
    ) -> RouterDecision:
        if (
            thesis.direction is ThesisDirection.NEUTRAL
            or quant.direction is ThesisDirection.NEUTRAL
        ):
            return self._abstain("neutral direction cannot produce an options strategy")
        if thesis.direction is not quant.direction:
            return self._abstain("AI and quant direction conflict")
        if quant.as_of > frame.observed_at or frame.observed_at - quant.as_of > _FRESHNESS:
            return self._abstain("quant signal is stale")

        quotes = tuple(getattr(frame, "option_quotes", ()))
        if not quotes:
            return self._abstain("no enriched option quotes are available")

        accepted, reasons = self._filter_quotes(quotes, frame.observed_at)
        candidates: list[StrategyCandidate] = []
        for left, right in combinations(accepted, 2):
            if left.expiration != right.expiration:
                continue
            if thesis.direction is ThesisDirection.BULLISH and not self._is_call_pair(left, right):
                continue
            if thesis.direction is ThesisDirection.BEARISH and not self._is_put_pair(left, right):
                continue
            candidate, pair_reasons = self._make_candidate(thesis, frame, left, right)
            if candidate is not None:
                candidates.append(candidate)
            reasons.extend(pair_reasons)

        if not candidates:
            return self._abstain(*self._stable_reasons(reasons))

        ranked = tuple(sorted(candidates, key=self._rank_key))
        return RouterDecision(
            outcome=RouterOutcome.SELECTED,
            selected=ranked[0],
            alternatives=ranked[1:],
        )

    @staticmethod
    def _abstain(*reasons: str) -> RouterDecision:
        return RouterDecision(
            outcome=RouterOutcome.ABSTAIN,
            reasons=tuple(dict.fromkeys(reasons)) or ("no valid strategy candidate",),
        )

    @staticmethod
    def _filter_quotes(
        quotes: tuple[OptionMarketQuote, ...], observed_at: datetime
    ) -> tuple[tuple[OptionMarketQuote, ...], list[str]]:
        accepted: list[OptionMarketQuote] = []
        reasons: list[str] = []
        for quote in quotes:
            if quote.as_of > observed_at or observed_at - quote.as_of > _FRESHNESS:
                reasons.append(f"stale quote: {quote.symbol}")
                continue
            if not quote.tradable:
                reasons.append(f"quote is not tradable: {quote.symbol}")
                continue
            if quote.bid <= 0 or quote.ask <= 0:
                reasons.append(f"non-positive bid/ask: {quote.symbol}")
                continue
            if quote.ask < quote.bid:
                reasons.append(f"crossed quote: {quote.symbol}")
                continue
            if quote.open_interest <= 0 or quote.volume <= 0:
                reasons.append(f"insufficient liquidity: {quote.symbol}")
                continue
            accepted.append(quote)
        return tuple(accepted), reasons

    @staticmethod
    def _is_call_pair(left: OptionMarketQuote, right: OptionMarketQuote) -> bool:
        return left.delta > 0 and right.delta > 0

    @staticmethod
    def _is_put_pair(left: OptionMarketQuote, right: OptionMarketQuote) -> bool:
        return left.delta < 0 and right.delta < 0

    def _make_candidate(
        self,
        thesis: GroundedThesis,
        frame: AgentMarketFrame,
        left: OptionMarketQuote,
        right: OptionMarketQuote,
    ) -> tuple[StrategyCandidate | None, list[str]]:
        reasons: list[str] = []
        low, high = sorted((left, right), key=lambda quote: (quote.strike, quote.symbol))
        if thesis.direction is ThesisDirection.BULLISH:
            long_quote, short_quote = low, high
        else:
            long_quote, short_quote = high, low

        dte = (long_quote.expiration - frame.observed_at.date()).days
        minimum_dte, maximum_dte = self._dte_band(thesis.horizon)
        if not minimum_dte <= dte <= maximum_dte:
            reasons.append(f"DTE outside {thesis.horizon.value} band: {dte}")
        if not Decimal("0.35") <= abs(long_quote.delta) <= Decimal("0.70"):
            reasons.append(f"long delta outside range: {long_quote.delta}")

        width = high.strike - low.strike
        width_percent = width / frame.price
        if not Decimal("0.02") <= width_percent <= Decimal("0.08"):
            reasons.append(f"width outside 2%-8% underlying: {width_percent}")

        debit = long_quote.ask - short_quote.bid
        if debit <= 0:
            reasons.append(f"debit is not positive: {debit}")
        elif debit > self._max_debit:
            reasons.append(f"debit exceeds max debit: {debit}")

        if reasons:
            return None, reasons

        liquidity_score = self._liquidity_score(long_quote, short_quote)
        slippage = (
            (long_quote.ask - long_quote.bid) + (short_quote.ask - short_quote.bid)
        ) / Decimal("2")
        utility = self._utility(
            thesis, frame, long_quote, short_quote, liquidity_score, slippage, debit, width
        )
        strategy_id = sha256_hex(
            {
                "version": UTILITY_VERSION,
                "direction": thesis.direction.value,
                "horizon": thesis.horizon.value,
                "long": long_quote.symbol,
                "short": short_quote.symbol,
            }
        )[:24]
        return (
            StrategyCandidate(
                strategy_id=strategy_id,
                direction=thesis.direction.value,
                horizon=thesis.horizon,
                long_symbol=long_quote.symbol,
                short_symbol=short_quote.symbol,
                limit_debit=debit,
                max_loss=debit * _HUNDRED,
                width=width,
                dte=dte,
                long_delta=long_quote.delta,
                liquidity_score=liquidity_score,
                slippage_estimate=slippage,
                utility_score=utility,
                rejection_reasons=(),
            ),
            [],
        )

    @staticmethod
    def _dte_band(horizon: Horizon) -> tuple[int, int]:
        return {
            Horizon.CATALYST: CATALYST_DTE,
            Horizon.SWING: SWING_DTE,
            Horizon.THEMATIC: THEMATIC_DTE,
        }[horizon]

    @staticmethod
    def _liquidity_score(long_quote: OptionMarketQuote, short_quote: OptionMarketQuote) -> Decimal:
        oi = min(long_quote.open_interest, short_quote.open_interest)
        volume = min(long_quote.volume, short_quote.volume)
        return min(Decimal("1"), Decimal(oi) / Decimal("1000")) * Decimal("0.5") + min(
            Decimal("1"), Decimal(volume) / Decimal("100")
        ) * Decimal("0.5")

    @staticmethod
    def _utility(
        thesis: GroundedThesis,
        frame: AgentMarketFrame,
        long_quote: OptionMarketQuote,
        short_quote: OptionMarketQuote,
        liquidity: Decimal,
        slippage: Decimal,
        debit: Decimal,
        width: Decimal,
    ) -> Decimal:
        alignment = thesis.confidence
        expiry_fit = Decimal("1") - abs(
            Decimal((short_quote.expiration - frame.observed_at.date()).days) - Decimal("60")
        ) / Decimal("450")
        expiry_fit = max(Decimal("0"), min(Decimal("1"), expiry_fit))
        slippage_score = max(Decimal("0"), Decimal("1") - slippage / debit)
        efficiency = max(Decimal("0"), min(Decimal("1"), Decimal("1") - debit / width))
        return (
            alignment * Decimal("0.40")
            + liquidity * Decimal("0.25")
            + expiry_fit * Decimal("0.15")
            + slippage_score * Decimal("0.10")
            + efficiency * Decimal("0.10")
        )

    @staticmethod
    def _rank_key(candidate: StrategyCandidate) -> tuple[Decimal, Decimal, str, str]:
        return (
            -candidate.utility_score,
            candidate.max_loss,
            candidate.long_symbol,
            candidate.short_symbol,
        )

    @staticmethod
    def _stable_reasons(reasons: list[str]) -> tuple[str, ...]:
        return tuple(sorted(set(reasons))) or ("no valid option spread passed the filters",)
