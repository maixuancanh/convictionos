import pytest

from convictionos.infrastructure.mcp_client import (
    McpServerConfig,
    StdioMcpToolClient,
)


def test_mcp_server_config_sets_paper_read_only_toolsets() -> None:
    config = McpServerConfig(command=("uvx", "alpaca-mcp-server==2.3.1"))

    assert config.env() == {
        "ALPACA_PAPER_TRADE": "true",
        "ALPACA_TOOLSETS": "account,assets,stock-data,options-data,corporate-actions,news",
    }


def test_mcp_server_config_rejects_trading_toolsets() -> None:
    with pytest.raises(ValueError, match="read-only"):
        McpServerConfig(toolsets=("account", "trading"))


def test_stdio_client_redacts_secrets_from_errors() -> None:
    client = StdioMcpToolClient(
        McpServerConfig(command=("missing-command",)),
        credential_markers=("paper-secret",),
    )

    with pytest.raises(RuntimeError) as error:
        client.sanitize_error("failed with paper-secret and private payload")

    assert "paper-secret" not in str(error.value)
    assert "private payload" not in str(error.value)
