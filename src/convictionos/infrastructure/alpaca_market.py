import re
from datetime import date, datetime, timedelta
from decimal import Decimal, DecimalException
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.application.agent import AgentMarketFrame, OptionQuote
from convictionos.application.market_clock import MarketSession
from convictionos.domain.canonical import sha256_hex
from convictionos.domain.market_data import (
    MarketDataCapability,
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.strategy import OptionMarketQuote

_OPTION_SYMBOL = re.compile(r"^(.+)(\d{6})([CP])(\d{8})$")
_OPTION_QUOTE_MAX_AGE = timedelta(minutes=5)


class OptionQuoteSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str
    symbols: tuple[str, ...]
    quotes: tuple[OptionMarketQuote, ...]
    observed_at: datetime
    capability: MarketDataCapability
    snapshot_hash: str

    @model_validator(mode="after")
    def validate_snapshot(self) -> "OptionQuoteSnapshot":
        if tuple(sorted(self.symbols)) != tuple(sorted(quote.symbol for quote in self.quotes)):
            raise ValueError("option snapshot symbols do not match requested symbols")
        if self.snapshot_hash != self.content_hash():
            raise ValueError("option snapshot hash does not match payload")
        return self

    def content_hash(self) -> str:
        quotes = []
        for quote in self.quotes:
            payload = quote.model_dump(mode="python")
            payload["expiration"] = quote.expiration.isoformat()
            quotes.append(payload)
        return sha256_hex(
            {
                "underlying": self.underlying,
                "symbols": self.symbols,
                "quotes": tuple(quotes),
                "observed_at": self.observed_at,
                "capability": self.capability.model_dump(mode="python"),
            }
        )


class PaperAccountState(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    status: str
    equity: Decimal = Field(ge=0)
    buying_power: Decimal = Field(ge=0)
    options_trading_level: int = Field(ge=0)


class PaperPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    quantity: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    asset_class: str | None = None
    side: str | None = None
    avg_entry_price: Decimal | None = None
    qty_available: Decimal | None = None

    @model_validator(mode="after")
    def validate_sanitized_position(self) -> "PaperPosition":
        result = self
        for name in (
            "quantity",
            "market_value",
            "unrealized_pl",
            "avg_entry_price",
            "qty_available",
        ):
            value = getattr(result, name)
            if value is not None and not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if result.asset_class == "us_option" and _OPTION_SYMBOL.fullmatch(result.symbol) is None:
            raise ValueError("malformed option position")
        if result.qty_available is not None and result.qty_available < 0:
            raise ValueError("qty_available must be non-negative")
        return result


class AlpacaPaperMarketGateway:
    """Read Alpaca market data and account state while enforcing paper trading."""

    def __init__(
        self,
        *,
        api_key_id: str = "",
        api_secret_key: str = "",
        oauth_token: str = "",
        paper_base_url: str = "https://paper-api.alpaca.markets",
        data_base_url: str = "https://data.alpaca.markets",
        option_data_feed: OptionsFeed = OptionsFeed.INDICATIVE,
    ) -> None:
        paper_url = paper_base_url.rstrip("/")
        data_url = data_base_url.rstrip("/")
        if paper_url != "https://paper-api.alpaca.markets":
            raise ValueError("Alpaca paper base_url must be https://paper-api.alpaca.markets")
        if data_url != "https://data.alpaca.markets":
            raise ValueError("Alpaca data base_url must be https://data.alpaca.markets")
        if not oauth_token and not (api_key_id and api_secret_key):
            raise ValueError("Alpaca paper credentials are required")
        headers = (
            {"Authorization": f"Bearer {oauth_token}"}
            if oauth_token
            else {
                "APCA-API-KEY-ID": api_key_id,
                "APCA-API-SECRET-KEY": api_secret_key,
            }
        )
        self._paper_client = httpx.AsyncClient(
            base_url=paper_url, headers=headers, timeout=10.0
        )
        self._data_client = httpx.AsyncClient(base_url=data_url, headers=headers, timeout=10.0)
        self._option_data_feed = OptionsFeed(option_data_feed)

    async def observe(
        self,
        underlying: str,
        *,
        expiration_gte: date,
        expiration_lte: date,
    ) -> AgentMarketFrame:
        stock_response = await self._data_client.get(
            f"/v2/stocks/{underlying}/snapshot", params={"feed": "iex"}
        )
        stock_response.raise_for_status()
        stock = stock_response.json()

        latest_trade = stock["latestTrade"]
        observed_at = self._parse_datetime(str(latest_trade["t"]))
        snapshots = await self._fetch_option_snapshots(
            underlying,
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
        )
        calls = self._parse_calls(snapshots)
        quote_rejections: set[str] = set()
        option_quotes: tuple[OptionMarketQuote, ...] = ()
        if any(self._has_enriched_fields(snapshot) for snapshot in snapshots.values()):
            contracts = await self._fetch_option_contracts(
                underlying,
                expiration_gte=expiration_gte,
                expiration_lte=expiration_lte,
            )
            parsed_quotes: list[OptionMarketQuote] = []
            for symbol, snapshot in snapshots.items():
                parsed_quote, rejection = self._parse_option_quote(
                    underlying, symbol, snapshot, contracts.get(symbol), observed_at
                )
                if rejection is not None:
                    quote_rejections.add(rejection)
                if parsed_quote is not None:
                    parsed_quotes.append(parsed_quote)
            option_quotes = tuple(
                sorted(
                    parsed_quotes,
                    key=lambda quote: (
                        quote.expiration,
                        quote.strike,
                        quote.option_type or "",
                        quote.symbol,
                    ),
                )
            )
        else:
            for symbol, snapshot in snapshots.items():
                _, rejection = self._parse_option_quote(
                    underlying, symbol, snapshot, None, observed_at
                )
                if rejection is not None:
                    quote_rejections.add(rejection)
        previous_bar = stock.get("prevDailyBar") or stock.get("previousDailyBar")
        if not isinstance(previous_bar, dict) or "c" not in previous_bar:
            raise KeyError("prevDailyBar")
        return AgentMarketFrame(
            underlying=underlying,
            price=Decimal(str(latest_trade["p"])),
            previous_close=Decimal(str(previous_bar["c"])),
            observed_at=observed_at,
            source=f"alpaca-{self._option_data_feed.value}",
            market_data=build_market_data_capability(
                underlying_feed=UnderlyingFeed.IEX,
                options_feed=self._option_data_feed,
                assessed_at=observed_at,
                missing_required_analytics="missing_required_analytics" in quote_rejections,
            ),
            calls=tuple(sorted(calls, key=lambda quote: (quote.expiration, quote.strike))),
            option_quotes=option_quotes,
            quote_rejections=tuple(sorted(quote_rejections)),
        )

    async def current_session(self, *, now: datetime) -> MarketSession:
        del now
        response = await self._paper_client.get("/v2/clock")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Alpaca clock response is malformed")
        return MarketSession(
            is_open=bool(payload["is_open"]),
            observed_at=self._parse_datetime(str(payload["timestamp"])),
            next_open=(
                self._parse_datetime(str(payload["next_open"]))
                if payload.get("next_open") is not None
                else None
            ),
            next_close=(
                self._parse_datetime(str(payload["next_close"]))
                if payload.get("next_close") is not None
                else None
            ),
            source="alpaca_clock",
        )

    async def _fetch_option_snapshots(
        self,
        underlying: str,
        *,
        expiration_gte: date,
        expiration_lte: date,
    ) -> dict[str, dict[str, Any]]:
        snapshots: dict[str, dict[str, Any]] = {}
        for option_type in ("call", "put"):
            page_token: str | None = None
            seen_tokens: set[str] = set()
            while True:
                params: dict[str, str | int] = {
                    "feed": self._option_data_feed.value,
                    "type": option_type,
                    "expiration_date_gte": expiration_gte.isoformat(),
                    "expiration_date_lte": expiration_lte.isoformat(),
                    "limit": 1000,
                }
                if page_token is not None:
                    params["page_token"] = page_token
                response = await self._data_client.get(
                    f"/v1beta1/options/snapshots/{underlying}", params=params
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("snapshots"), dict
                ):
                    raise ValueError("Alpaca option chain response is malformed")
                for symbol, snapshot in payload["snapshots"].items():
                    if isinstance(symbol, str) and isinstance(snapshot, dict):
                        snapshots.setdefault(symbol, snapshot)
                next_token = payload.get("next_page_token")
                if next_token in (None, ""):
                    break
                if not isinstance(next_token, str) or next_token in seen_tokens:
                    raise ValueError("Alpaca option chain pagination token is malformed")
                seen_tokens.add(next_token)
                page_token = next_token
        return snapshots

    async def _fetch_option_contracts(
        self,
        underlying: str,
        *,
        expiration_gte: date,
        expiration_lte: date,
    ) -> dict[str, dict[str, Any]]:
        contracts: dict[str, dict[str, Any]] = {}
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            params: dict[str, str | int] = {
                "underlying_symbols": underlying,
                "status": "active",
                "expiration_date_gte": expiration_gte.isoformat(),
                "expiration_date_lte": expiration_lte.isoformat(),
                "limit": 10000,
            }
            if page_token is not None:
                params["page_token"] = page_token
            response = await self._paper_client.get("/v2/options/contracts", params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(
                payload.get("option_contracts"), list
            ):
                raise ValueError("Alpaca option contracts response is malformed")
            for contract in payload["option_contracts"]:
                if isinstance(contract, dict) and isinstance(contract.get("symbol"), str):
                    contracts.setdefault(contract["symbol"], contract)
            next_token = payload.get("page_token")
            if next_token in (None, ""):
                break
            if not isinstance(next_token, str) or next_token in seen_tokens:
                raise ValueError("Alpaca option contracts pagination token is malformed")
            seen_tokens.add(next_token)
            page_token = next_token
        return contracts

    async def get_account(self) -> PaperAccountState:
        response = await self._paper_client.get("/v2/account")
        response.raise_for_status()
        payload = response.json()
        return PaperAccountState(
            account_id=str(payload["id"]),
            status=str(payload["status"]),
            equity=Decimal(str(payload["equity"])),
            buying_power=Decimal(str(payload["buying_power"])),
            options_trading_level=int(payload.get("options_trading_level", 0)),
        )

    async def get_positions(self) -> tuple[PaperPosition, ...]:
        response = await self._paper_client.get("/v2/positions")
        response.raise_for_status()
        return tuple(
            PaperPosition(
                symbol=str(item["symbol"]),
                quantity=Decimal(str(item["qty"])),
                market_value=Decimal(str(item["market_value"])),
                unrealized_pl=Decimal(str(item["unrealized_pl"])),
                asset_class=(
                    str(item["asset_class"]) if item.get("asset_class") is not None else None
                ),
                side=(str(item["side"]) if item.get("side") is not None else None),
                avg_entry_price=(
                    Decimal(str(item["avg_entry_price"]))
                    if item.get("avg_entry_price") is not None
                    else None
                ),
                qty_available=(
                    Decimal(str(item["qty_available"]))
                    if item.get("qty_available") is not None
                    else None
                ),
            )
            for item in response.json()
        )

    async def get_option_quotes(self, symbols: tuple[str, ...]) -> OptionQuoteSnapshot:
        requested = tuple(dict.fromkeys(symbols))
        if not requested or any(not isinstance(symbol, str) or not symbol for symbol in requested):
            raise ValueError("option symbols are required")
        matches = [_OPTION_SYMBOL.match(symbol) for symbol in requested]
        if any(match is None for match in matches):
            raise ValueError("option symbols must share one valid underlying")
        underlyings = {
            symbol[: match.start(2)]
            for symbol, match in zip(requested, matches, strict=True)
            if match
        }
        if len(underlyings) != 1:
            raise ValueError("option symbols must share one valid underlying")
        underlying = next(iter(underlyings))
        response = await self._data_client.get(
            "/v1beta1/options/snapshots",
            params={"symbols": ",".join(requested), "feed": self._option_data_feed.value},
        )
        response.raise_for_status()
        payload = response.json()
        snapshots = payload.get("snapshots") if isinstance(payload, dict) else None
        if not isinstance(snapshots, dict) or set(snapshots) != set(requested):
            raise ValueError("Alpaca option snapshot response is incomplete")
        quotes: list[OptionMarketQuote] = []
        for symbol in requested:
            snapshot = snapshots[symbol]
            quote = self._direct_option_quote(underlying, symbol, snapshot)
            quotes.append(quote)
        observed_at = max(quote.as_of for quote in quotes)
        if any(observed_at - quote.as_of > timedelta(seconds=1) for quote in quotes):
            raise ValueError("option snapshot timestamps are not aligned")
        if any(observed_at - quote.as_of > _OPTION_QUOTE_MAX_AGE for quote in quotes):
            raise ValueError("option snapshot is stale")
        capability = build_market_data_capability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=self._option_data_feed,
            assessed_at=observed_at,
        )
        provisional = OptionQuoteSnapshot.model_construct(
            snapshot_hash="provisional",
            underlying=underlying,
            symbols=tuple(sorted(requested)),
            quotes=tuple(quotes),
            observed_at=observed_at,
            capability=capability,
        )
        return OptionQuoteSnapshot(
            snapshot_hash=provisional.content_hash(),
            underlying=underlying,
            symbols=tuple(sorted(requested)),
            quotes=tuple(quotes),
            observed_at=observed_at,
            capability=capability,
        )

    @classmethod
    def _direct_option_quote(
        cls, underlying: str, symbol: str, snapshot: Any
    ) -> OptionMarketQuote:
        match = _OPTION_SYMBOL.match(symbol)
        if match is None or not isinstance(snapshot, dict):
            raise ValueError("invalid option snapshot")
        latest = snapshot.get("latestQuote")
        greeks = snapshot.get("greeks")
        if not isinstance(latest, dict) or not isinstance(greeks, dict):
            raise ValueError("option snapshot is missing quote analytics")
        bid = cls._finite_decimal(latest["bp"])
        ask = cls._finite_decimal(latest["ap"])
        as_of = cls._parse_datetime(str(latest["t"]))
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError("option snapshot quote is crossed or non-tradable")
        option_type: Literal["call", "put"] = "call" if match.group(3) == "C" else "put"
        return OptionMarketQuote(
            symbol=symbol,
            option_type=option_type,
            strike=Decimal(match.group(4)) / Decimal("1000"),
            expiration=datetime.strptime(match.group(2), "%y%m%d").date(),
            bid=bid,
            ask=ask,
            delta=cls._finite_decimal(greeks["delta"]),
            implied_volatility=cls._finite_decimal(snapshot["impliedVolatility"]),
            open_interest=cls._nonnegative_int(snapshot["openInterest"]),
            volume=cls._nonnegative_int(snapshot["volume"]),
            as_of=as_of,
            tradable=True,
        )

    @classmethod
    def _parse_calls(cls, snapshots: dict[str, Any]) -> list[OptionQuote]:
        calls: list[OptionQuote] = []
        for symbol, snapshot in snapshots.items():
            match = _OPTION_SYMBOL.match(symbol)
            quote = snapshot.get("latestQuote") if isinstance(snapshot, dict) else None
            if match is None or match.group(3) != "C" or not isinstance(quote, dict):
                continue
            try:
                bid = cls._finite_decimal(quote["bp"])
                ask = cls._finite_decimal(quote["ap"])
                as_of = cls._parse_datetime(str(quote["t"]))
                expiration = datetime.strptime(match.group(2), "%y%m%d").date()
                strike = Decimal(match.group(4)) / Decimal("1000")
            except (DecimalException, KeyError, TypeError, ValueError):
                continue
            if bid <= 0 or ask < bid:
                continue
            calls.append(
                OptionQuote(
                    symbol=symbol,
                    strike=strike,
                    expiration=expiration,
                    bid_price=bid,
                    ask_price=ask,
                    as_of=as_of,
                )
            )
        return calls

    @staticmethod
    def _has_enriched_fields(snapshot: Any) -> bool:
        return (
            isinstance(snapshot, dict)
            and isinstance(snapshot.get("latestQuote"), dict)
            and isinstance(snapshot.get("greeks"), dict)
        )

    @classmethod
    def _parse_option_quote(
        cls,
        underlying: str,
        symbol: str,
        snapshot: Any,
        contract: dict[str, Any] | None,
        observed_at: datetime,
    ) -> tuple[OptionMarketQuote | None, str | None]:
        match = _OPTION_SYMBOL.match(symbol)
        if match is None:
            return None, "contract_mismatch"
        if not isinstance(snapshot, dict):
            return None, "invalid_option_quote"
        quote = snapshot.get("latestQuote")
        greeks = snapshot.get("greeks")
        if (
            not isinstance(quote, dict)
            or not isinstance(greeks, dict)
            or "delta" not in greeks
            or "impliedVolatility" not in snapshot
            or "openInterest" not in snapshot
            or "volume" not in snapshot
        ):
            return None, "missing_required_analytics"
        if contract is None:
            return None, "contract_mismatch"
        try:
            option_type: Literal["call", "put"] = (
                "call" if match.group(3) == "C" else "put"
            )
            expiration = datetime.strptime(match.group(2), "%y%m%d").date()
            strike = Decimal(match.group(4)) / Decimal("1000")
            bid = cls._finite_decimal(quote["bp"])
            ask = cls._finite_decimal(quote["ap"])
            delta = cls._finite_decimal(greeks["delta"])
            implied_volatility = cls._finite_decimal(snapshot["impliedVolatility"])
            open_interest = cls._nonnegative_int(snapshot["openInterest"])
            volume = cls._nonnegative_int(snapshot["volume"])
            as_of = cls._parse_datetime(str(quote["t"]))
            contract_expiration = date.fromisoformat(str(contract["expiration_date"]))
            contract_strike = cls._finite_decimal(contract["strike_price"])
            contract_type = contract["type"]
            contract_underlying = contract["underlying_symbol"]
            tradable = contract["tradable"]
            status = contract["status"]
        except (DecimalException, KeyError, TypeError, ValueError):
            return None, "invalid_option_quote"
        if (
            as_of.tzinfo is None
            or as_of > observed_at
            or observed_at - as_of > _OPTION_QUOTE_MAX_AGE
        ):
            return None, "stale_option_quote"
        if open_interest <= 0 or volume <= 0:
            return None, "illiquid_option_quote"
        if (
            bid <= 0 or ask <= 0 or ask < bid or implied_volatility <= 0
            or not isinstance(tradable, bool) or not isinstance(status, str)
            or status.lower() != "active"
            or (option_type == "call" and delta <= 0)
            or (option_type == "put" and delta >= 0)
        ):
            return None, "invalid_option_quote"
        if (
            contract_type != option_type or contract_underlying != underlying
            or contract_expiration != expiration or contract_strike != strike
        ):
            return None, "contract_mismatch"
        return OptionMarketQuote(
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            expiration=expiration,
            bid=bid,
            ask=ask,
            delta=delta,
            implied_volatility=implied_volatility,
            open_interest=open_interest,
            volume=volume,
            as_of=as_of,
            tradable=tradable,
        ), None

    @staticmethod
    def _finite_decimal(value: Any) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("decimal value must be finite")
        return result

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("integer value must not be boolean")
        result = int(value)
        if str(result) != str(value) and not isinstance(value, int):
            raise ValueError("integer value is malformed")
        if result < 0:
            raise ValueError("integer value must be non-negative")
        return result

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    async def aclose(self) -> None:
        await self._paper_client.aclose()
        await self._data_client.aclose()
