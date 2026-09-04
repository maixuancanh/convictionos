from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.infrastructure.mcp_client import McpToolClient

ALPACA_MCP_VERSION = "alpaca-mcp-server==2.3.1"
WRITE_LIKE_PREFIXES = (
    "create",
    "update",
    "delete",
    "cancel",
    "close",
    "exercise",
    "submit",
    "replace",
)


class AlpacaMcpCapability(StrEnum):
    ACCOUNT = "account"
    CLOCK = "clock"
    OPTION_CHAIN = "option_chain"
    NEWS = "news"


CAPABILITY_TO_TOOL = {
    AlpacaMcpCapability.ACCOUNT: "get_account",
    AlpacaMcpCapability.CLOCK: "get_clock",
    AlpacaMcpCapability.OPTION_CHAIN: "get_option_chain",
    AlpacaMcpCapability.NEWS: "get_news",
}


class McpObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_version: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    request_hash: str = Field(min_length=1)
    observed_at: datetime
    effective_at: datetime | None = None
    published_at: datetime | None = None
    facts: dict[str, Any]
    limitations: tuple[str, ...]
    content_hash: str

    @model_validator(mode="after")
    def validate_times(self) -> "McpObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_hash"})


class AlpacaMcpGateway:
    def __init__(
        self,
        *,
        client: McpToolClient,
        expected_schema_hash: str,
        credential_markers: Iterable[str] = (),
    ) -> None:
        self._client = client
        self._expected_schema_hash = expected_schema_hash
        self._credential_markers = tuple(credential_markers)
        self._tools: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        await self._client.initialize()
        tools = await self._client.list_tools()
        write_like = [tool["name"] for tool in tools if _is_write_like(str(tool["name"]))]
        if write_like:
            raise ValueError("write-like MCP tools are not allowed")
        actual_hash = canonical_schema_hash(tools)
        if actual_hash != self._expected_schema_hash:
            raise ValueError("Alpaca MCP schema drift detected")
        self._tools = {str(tool["name"]): tool for tool in tools}

    async def read(
        self,
        *,
        capability: AlpacaMcpCapability,
        arguments: dict[str, object],
        observed_at: datetime,
    ) -> McpObservation:
        tool_name = CAPABILITY_TO_TOOL[capability]
        if tool_name not in self._tools:
            raise ValueError("requested MCP capability is unavailable")
        try:
            result = await self._client.call_tool(tool_name, arguments)
        except Exception as error:
            raise RuntimeError("Alpaca MCP read failed") from _redacted_error(
                error, self._credential_markers
            )
        if not isinstance(result, dict):
            raise RuntimeError("Alpaca MCP returned malformed content")
        facts = {str(key): value for key, value in result.items()}
        observation = McpObservation(
            provider_version=ALPACA_MCP_VERSION,
            tool_name=tool_name,
            request_hash=sha256_hex(arguments),
            observed_at=observed_at,
            effective_at=_parse_optional_time(facts.get("effective_at")),
            published_at=_parse_optional_time(facts.get("published_at")),
            facts=facts,
            limitations=("mcp_read_model",),
            content_hash="pending",
        )
        return observation.model_copy(
            update={"content_hash": sha256_hex(observation.canonical_payload())}
        )


def canonical_schema_hash(tools: Iterable[dict[str, Any]]) -> str:
    allowed = [
        {
            "name": str(tool["name"]),
            "inputSchema": tool.get("inputSchema", {}),
            "risk": tool.get("risk", "unknown"),
        }
        for tool in tools
        if not _is_write_like(str(tool.get("name", "")))
    ]
    return sha256_hex(sorted(allowed, key=lambda item: item["name"]))


def _is_write_like(name: str) -> bool:
    return name.startswith(WRITE_LIKE_PREFIXES)


def _parse_optional_time(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _redacted_error(error: Exception, credential_markers: tuple[str, ...]) -> Exception:
    message = str(error)
    for marker in credential_markers:
        if marker:
            message = message.replace(marker, "[REDACTED]")
    if "provider payload" in message:
        message = "provider payload redacted"
    return RuntimeError(message)
