import pytest

from convictionos.api.main import build_model_gateway, validate_agent_configuration
from convictionos.infrastructure.model_openai_compatible import OpenAICompatibleModelGateway
from convictionos.settings import Settings


def test_api_entrypoint_uses_selector_event_loop_on_windows() -> None:
    import asyncio
    import sys

    if sys.platform == "win32":
        assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)


@pytest.mark.parametrize("provider", ["openai", "gemini", "claude"])
def test_build_model_gateway_selects_provider(provider: str) -> None:
    settings = Settings(
        ai_provider=provider,
        ai_model="test-model",
        ai_api_key="secret",
        ai_base_url="https://api.openai.com/v1",
    )

    gateway = build_model_gateway(settings)

    assert gateway.provider == provider
    assert gateway.model == "test-model"


def test_build_model_gateway_passes_timeout_and_output_limit() -> None:
    settings = Settings(
        ai_provider="claude",
        ai_model="test-model",
        ai_api_key="secret",
        ai_timeout_seconds=7.5,
        ai_max_output_tokens=321,
    )

    gateway = build_model_gateway(settings)

    assert gateway.max_output_tokens == 321
    assert gateway._client.timeout.read == 7.5


def test_build_model_gateway_selects_openai_compatible_gateway_for_custom_base_url() -> None:
    settings = Settings(
        ai_provider="openai",
        ai_model="openai/gpt-5.5",
        ai_api_key="tokenrouter-secret",
        ai_base_url="https://api.tokenrouter.com/v1",
    )

    gateway = build_model_gateway(settings)

    assert isinstance(gateway, OpenAICompatibleModelGateway)
    assert gateway.base_url == "https://api.tokenrouter.com/v1"


def test_build_model_gateway_supports_openrouter_provider() -> None:
    settings = Settings(
        ai_provider="openrouter",
        ai_model="anthropic/claude-sonnet-4.5",
        ai_api_key="openrouter-secret",
    )

    gateway = build_model_gateway(settings)

    assert isinstance(gateway, OpenAICompatibleModelGateway)
    assert gateway.base_url == "https://openrouter.ai/api/v1"
    assert gateway.provider == "openrouter"
    assert gateway.provider_controls == {
        "require_parameters": True,
        "allow_fallbacks": False,
        "data_collection": "deny",
    }


def test_enabled_agent_requires_complete_ai_configuration() -> None:
    settings = Settings(
        agent_enabled=True,
        broker_mode="alpaca_paper",
        ai_provider="",
    )

    with pytest.raises(ValueError, match="AI_PROVIDER"):
        validate_agent_configuration(settings)


def test_fake_agent_does_not_require_ai_configuration() -> None:
    settings = Settings(agent_enabled=True, broker_mode="fake")

    validate_agent_configuration(settings)


def test_api_key_is_not_present_in_settings_repr() -> None:
    settings = Settings(ai_api_key="secret")

    assert "secret" not in repr(settings)
