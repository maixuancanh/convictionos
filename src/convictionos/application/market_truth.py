from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.strategy import OptionMarketQuote
from convictionos.infrastructure.alpaca_mcp import McpObservation

if TYPE_CHECKING:
    from convictionos.application.agent import AgentMarketFrame, PaperAccount


class MarketTruthOutcome(StrEnum):
    CORROBORATED = "corroborated"
    CONFLICTED = "conflicted"
    MCP_UNAVAILABLE = "mcp_unavailable"


class MarketTruthPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: MarketTruthOutcome
    observed_at: datetime
    rest_source_hash: str = Field(min_length=1)
    mcp_observation_hashes: tuple[str, ...]
    reasons: tuple[str, ...]
    sources: tuple[str, ...]
    truth_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_truth_hash(self) -> MarketTruthPacket:
        if self.truth_hash != "pending" and self.truth_hash != self.content_hash():
            raise ValueError("market truth hash does not match payload")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"truth_hash"})

    def content_hash(self) -> str:
        return sha256_hex(self.canonical_payload())


class MarketTruthObservationPort(Protocol):
    async def observe_market_truth(
        self, frame: AgentMarketFrame, account: PaperAccount, *, observed_at: datetime
    ) -> tuple[McpObservation, ...]: ...


class MarketTruthService:
    def __init__(self, *, required_mcp: bool) -> None:
        self._required_mcp = required_mcp

    def cross_check(
        self,
        *,
        frame: AgentMarketFrame,
        account: PaperAccount,
        observations: Iterable[McpObservation],
        observed_at: datetime,
    ) -> MarketTruthPacket:
        ordered_observations = tuple(observations)
        if self._required_mcp and not ordered_observations:
            return self._packet(
                outcome=MarketTruthOutcome.MCP_UNAVAILABLE,
                frame=frame,
                observations=ordered_observations,
                observed_at=observed_at,
                reasons=("required_mcp_unavailable",),
                sources=("alpaca_rest",),
            )

        reasons: list[str] = []
        for observation in ordered_observations:
            facts = observation.facts
            if observation.tool_name == "get_account":
                reasons.extend(_account_reasons(account, facts))
            elif observation.tool_name == "get_clock":
                reasons.extend(_clock_reasons(frame, facts))
            elif observation.tool_name == "get_option_chain":
                reasons.extend(_option_reasons(frame.option_quotes, facts))
            elif observation.tool_name == "get_news":
                reasons.extend(_news_reasons(facts))

        outcome = (
            MarketTruthOutcome.CONFLICTED
            if reasons
            else MarketTruthOutcome.CORROBORATED
        )
        return self._packet(
            outcome=outcome,
            frame=frame,
            observations=ordered_observations,
            observed_at=observed_at,
            reasons=tuple(sorted(dict.fromkeys(reasons))),
            sources=("alpaca_rest", "alpaca_mcp") if ordered_observations else ("alpaca_rest",),
        )

    @staticmethod
    def _packet(
        *,
        outcome: MarketTruthOutcome,
        frame: AgentMarketFrame,
        observations: tuple[McpObservation, ...],
        observed_at: datetime,
        reasons: tuple[str, ...],
        sources: tuple[str, ...],
    ) -> MarketTruthPacket:
        packet = MarketTruthPacket(
            outcome=outcome,
            observed_at=observed_at,
            rest_source_hash=frame.snapshot_hash(),
            mcp_observation_hashes=tuple(
                observation.content_hash for observation in observations
            ),
            reasons=reasons,
            sources=sources,
            truth_hash="pending",
        )
        return packet.model_copy(update={"truth_hash": packet.content_hash()})


def _account_reasons(account: PaperAccount, facts: dict[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    observed_id = _string_fact(facts, "id", "account_id")
    if observed_id is not None and observed_id != account.account_id:
        reasons.append("account_id_mismatch")
    observed_status = _string_fact(facts, "status")
    if observed_status is not None and observed_status.upper() != account.status.upper():
        reasons.append("account_status_mismatch")
    observed_buying_power = _decimal_fact(facts, "buying_power")
    if observed_buying_power is not None and observed_buying_power != account.buying_power:
        reasons.append("buying_power_mismatch")
    observed_cash = _decimal_fact(facts, "cash")
    if observed_cash is not None and observed_cash > account.buying_power:
        reasons.append("cash_exceeds_buying_power")
    return tuple(reasons)


def _clock_reasons(frame: AgentMarketFrame, facts: dict[str, Any]) -> tuple[str, ...]:
    reasons: list[str] = []
    is_open = facts.get("is_open", facts.get("session_open"))
    if is_open is False:
        reasons.append("clock_market_closed")
    timestamp = _datetime_fact(facts, "timestamp")
    if timestamp is not None and timestamp.date() != frame.observed_at.date():
        reasons.append("clock_session_date_mismatch")
    return tuple(reasons)


def _option_reasons(
    quotes: tuple[OptionMarketQuote, ...], facts: dict[str, Any]
) -> tuple[str, ...]:
    symbol = _string_fact(facts, "symbol", "option_symbol")
    if symbol is None:
        return ()
    quote = next((candidate for candidate in quotes if candidate.symbol == symbol), None)
    if quote is None:
        return (f"option_symbol_missing:{symbol}",)
    reasons: list[str] = []
    expiration = _date_string_fact(facts, "expiration_date", "expiration")
    if expiration is not None and expiration != quote.expiration.isoformat():
        reasons.append(f"option_expiration_mismatch:{symbol}")
    strike = _decimal_fact(facts, "strike_price", "strike")
    if strike is not None and strike != quote.strike:
        reasons.append(f"option_strike_mismatch:{symbol}")
    option_type = _string_fact(facts, "type", "option_type")
    if (
        option_type is not None
        and quote.option_type is not None
        and option_type.lower() != quote.option_type.lower()
    ):
        reasons.append(f"option_type_mismatch:{symbol}")
    quote_time = _datetime_fact(facts, "latest_quote_timestamp", "as_of")
    if quote_time is not None and quote_time != quote.as_of:
        reasons.append(f"option_quote_timestamp_mismatch:{symbol}")
    return tuple(reasons)


def _news_reasons(facts: dict[str, Any]) -> tuple[str, ...]:
    if _string_fact(facts, "id", "news_id") is None:
        return ("news_id_missing",)
    if _datetime_fact(facts, "published_at") is None:
        return ("news_timestamp_missing",)
    return ()


def _string_fact(facts: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = facts.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _date_string_fact(facts: dict[str, Any], *names: str) -> str | None:
    value = _string_fact(facts, *names)
    if value is None:
        return None
    return value[:10]


def _decimal_fact(facts: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = facts.get(name)
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def _datetime_fact(facts: dict[str, Any], *names: str) -> datetime | None:
    value = _string_fact(facts, *names)
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
