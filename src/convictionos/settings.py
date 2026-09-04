from decimal import Decimal

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from convictionos.domain.competition import COMPETITION_MANDATE_VERSION
from convictionos.domain.market_data import OptionsFeed


class CompetitionStartupInvariants(BaseModel):
    model_config = ConfigDict(frozen=True)

    competition_account_id: str
    competition_baseline_equity: Decimal
    competition_mandate_version: str
    alpaca_paper_base_url: str
    alpaca_live_trade: bool
    alpaca_cli_revision: str
    alpaca_cli_path: str
    alpaca_mcp_version: str
    alpaca_mcp_command: str
    alpaca_mcp_schema_hash: str

    @model_validator(mode="after")
    def validate_competition_startup(self) -> "CompetitionStartupInvariants":
        if not self.competition_account_id.strip():
            raise ValueError("COMPETITION_ACCOUNT_ID is required for the paper agent")
        if self.competition_baseline_equity != Decimal("100000.00"):
            raise ValueError("competition baseline equity must be 100000.00")
        if self.alpaca_paper_base_url.rstrip("/") != "https://paper-api.alpaca.markets":
            raise ValueError(
                "ALPACA_PAPER_BASE_URL must be https://paper-api.alpaca.markets"
            )
        if self.alpaca_live_trade:
            raise ValueError("ALPACA_LIVE_TRADE must remain false")
        if self.competition_mandate_version != COMPETITION_MANDATE_VERSION:
            raise ValueError(
                f"competition mandate version must be {COMPETITION_MANDATE_VERSION}"
            )
        required_pins = {
            "ALPACA_CLI_REVISION": self.alpaca_cli_revision,
            "ALPACA_CLI_PATH": self.alpaca_cli_path,
            "ALPACA_MCP_VERSION": self.alpaca_mcp_version,
            "ALPACA_MCP_COMMAND": self.alpaca_mcp_command,
            "ALPACA_MCP_SCHEMA_HASH": self.alpaca_mcp_schema_hash,
        }
        for name, value in required_pins.items():
            if not value.strip():
                raise ValueError(f"{name} is required for the paper agent")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://convictionos:convictionos@localhost:54329/convictionos"
    )
    broker_mode: str = "fake"
    alpaca_oauth_token: str = ""
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_option_data_feed: OptionsFeed = OptionsFeed.INDICATIVE
    competition_account_id: str = ""
    competition_baseline_equity: Decimal = Decimal("100000.00")
    competition_mandate_version: str = COMPETITION_MANDATE_VERSION
    alpaca_live_trade: bool = False
    alpaca_cli_revision: str = ""
    alpaca_cli_path: str = ""
    alpaca_mcp_version: str = ""
    alpaca_mcp_command: str = ""
    alpaca_mcp_schema_hash: str = ""
    agent_enabled: bool = False
    require_agent_startup_invariants: bool = True
    agent_control_token: str = ""
    agent_underlying: str = "SPY"
    competition_workspace_id: str = "competition"
    runtime_agent_id: str = "convictionos"
    runtime_release_sha: str = "unknown"
    runtime_instance_id: str = "local-worker"
    ai_provider: str = ""
    ai_model: str = ""
    ai_api_key: SecretStr = SecretStr("")
    ai_base_url: str = ""
    ai_timeout_seconds: float = 20.0
    ai_max_output_tokens: int = 1200
    ai_prompt_version: str = "grounded-thesis-v1"
    agent_interval_seconds: float = 300.0
    exit_policy_version: str = "exit-policy-v1"

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                return "postgresql+psycopg://" + value[len(scheme) :]
        return value

    @field_validator("alpaca_paper_base_url")
    @classmethod
    def normalize_alpaca_paper_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if normalized == "https://paper-api.alpaca.markets/v2":
            return "https://paper-api.alpaca.markets"
        return normalized

    @field_validator("alpaca_option_data_feed", mode="before")
    @classmethod
    def validate_option_data_feed(cls, value: object) -> OptionsFeed:
        if not isinstance(value, str):
            raise ValueError("ALPACA_OPTION_DATA_FEED must be indicative or opra")
        try:
            feed = OptionsFeed(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("ALPACA_OPTION_DATA_FEED must be indicative or opra") from exc
        if feed not in (OptionsFeed.INDICATIVE, OptionsFeed.OPRA):
            raise ValueError("ALPACA_OPTION_DATA_FEED must be indicative or opra")
        return feed

    @field_validator("exit_policy_version")
    @classmethod
    def validate_exit_policy_version(cls, value: str) -> str:
        if value != "exit-policy-v1":
            raise ValueError("EXIT_POLICY_VERSION must be exit-policy-v1")
        return value

    @model_validator(mode="after")
    def validate_agent_startup_invariants(self) -> "Settings":
        if (
            self.require_agent_startup_invariants
            and self.agent_enabled
            and self.broker_mode == "alpaca_paper"
            and self.agent_control_token
            and self.ai_provider
            and self.ai_model
            and self.ai_api_key.get_secret_value()
        ):
            CompetitionStartupInvariants(
                competition_account_id=self.competition_account_id,
                competition_baseline_equity=self.competition_baseline_equity,
                competition_mandate_version=self.competition_mandate_version,
                alpaca_paper_base_url=self.alpaca_paper_base_url,
                alpaca_live_trade=self.alpaca_live_trade,
                alpaca_cli_revision=self.alpaca_cli_revision,
                alpaca_cli_path=self.alpaca_cli_path,
                alpaca_mcp_version=self.alpaca_mcp_version,
                alpaca_mcp_command=self.alpaca_mcp_command,
                alpaca_mcp_schema_hash=self.alpaca_mcp_schema_hash,
            )
        return self

    def __repr__(self) -> str:
        return "Settings(<redacted>)"
