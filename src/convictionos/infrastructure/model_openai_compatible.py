import logging
from typing import Any

import httpx

from convictionos.application.model_gateway import IntelligenceRequest, ModelResult
from convictionos.infrastructure.model_http import (
    SYSTEM_POLICY,
    ModelHTTP,
    evidence_prompt,
    parse_thesis,
    thesis_schema,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleModelGateway(ModelHTTP):
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        provider_name: str = "openai-compatible",
        provider_controls: dict[str, object] | None = None,
        max_output_tokens: int = 1200,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(http_client=http_client)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.provider = provider_name
        self.provider_controls = provider_controls
        if provider_name == "openrouter" and provider_controls is None:
            self.provider_controls = {
                "require_parameters": True,
                "allow_fallbacks": True,
                "data_collection": "deny",
            }

    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_POLICY},
                {"role": "user", "content": evidence_prompt(request)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "grounded_thesis",
                    "strict": True,
                    "schema": thesis_schema(),
                },
            },
        }
        if self.provider_controls is not None:
            payload["provider"] = self.provider_controls
        if self.provider == "openrouter":
            payload["reasoning"] = {"exclude": True}
        response = await self._post_json(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload=payload,
        )
        text = _chat_content(response.get("choices"))
        if text is None:
            choices = response.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else {}
            message = first.get("message") if isinstance(first, dict) else {}
            logger.warning(
                "model gateway returned no structured content provider=%s model=%s "
                "finish_reason=%s native_finish_reason=%s has_reasoning=%s",
                self.provider,
                response.get("model", self.model),
                first.get("finish_reason") if isinstance(first, dict) else None,
                first.get("native_finish_reason") if isinstance(first, dict) else None,
                isinstance(message, dict) and bool(message.get("reasoning")),
            )
            from convictionos.application.model_gateway import IntelligenceUnavailable

            raise IntelligenceUnavailable("openai-compatible missing output")
        usage = response.get("usage")
        usage_map = usage if isinstance(usage, dict) else {}
        return ModelResult(
            thesis=parse_thesis(self.provider, text),
            provider=self.provider,
            model=self.model,
            input_tokens=_integer(usage_map.get("prompt_tokens")),
            output_tokens=_integer(usage_map.get("completion_tokens")),
        )


def _chat_content(choices: object) -> str | None:
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) and content else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
