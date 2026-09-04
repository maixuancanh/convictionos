from collections.abc import Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Protocol, cast

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

READ_ONLY_TOOLSETS = (
    "account",
    "assets",
    "stock-data",
    "options-data",
    "corporate-actions",
    "news",
)
WRITE_TOOLSETS = {"trading", "watchlists", "locates"}


class McpToolClient(Protocol):
    async def initialize(self) -> None: ...

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> Any: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class McpServerConfig:
    command: tuple[str, ...] = ("uvx", "alpaca-mcp-server==2.3.1")
    toolsets: tuple[str, ...] = READ_ONLY_TOOLSETS

    def __post_init__(self) -> None:
        if any(toolset in WRITE_TOOLSETS for toolset in self.toolsets):
            raise ValueError("Alpaca MCP server must be configured with read-only toolsets")

    def env(self) -> dict[str, str]:
        return {
            "ALPACA_PAPER_TRADE": "true",
            "ALPACA_TOOLSETS": ",".join(self.toolsets),
        }


class StdioMcpToolClient:
    def __init__(
        self,
        config: McpServerConfig,
        *,
        credential_markers: Iterable[str] = (),
    ) -> None:
        self._config = config
        self._credential_markers = tuple(credential_markers)
        self._session: Any | None = None
        self._stack: AsyncExitStack | None = None

    async def initialize(self) -> None:
        if self._session is not None:
            return
        stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self._config.command[0],
            args=list(self._config.command[1:]),
            env=self._config.env(),
        )
        read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        self._stack = stack
        self._session = session

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("MCP client has not been initialized")
        result = await self._session.list_tools()
        return [cast(dict[str, Any], tool.model_dump(mode="json")) for tool in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, object]) -> Any:
        if self._session is None:
            raise RuntimeError("MCP client has not been initialized")
        result = await self._session.call_tool(name, arguments)
        return result.model_dump(mode="json")

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    def sanitize_error(self, message: str) -> None:
        del message
        raise RuntimeError("MCP client operation failed")
