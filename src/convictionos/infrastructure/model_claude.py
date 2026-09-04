from typing import Any

import httpx

from convictionos.application.model_gateway import (
    IntelligenceRequest,
    IntelligenceUnavailable,
    ModelResult,
)
from convictionos.infrastructure.model_http import (
    SYSTEM_POLICY,
    ModelHTTP,
    evidence_prompt,
    parse_thesis,
    thesis_schema,
)


class ClaudeModelGateway(ModelHTTP):
    provider = "claude"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        max_output_tokens: int = 1200,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(http_client=http_client)
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._api_key = api_key

    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": SYSTEM_POLICY,
            "messages": [{"role": "user", "content": evidence_prompt(request)}],
            "output_config": {"format": {"type": "json_schema", "schema": thesis_schema()}},
        }
        response = await self._post_json(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            payload=payload,
        )
        text = _content_text(response.get("content"))
        if text is None:
            raise IntelligenceUnavailable("claude refusal or missing output")
        usage = response.get("usage")
        usage_map = usage if isinstance(usage, dict) else {}
        return ModelResult(
            thesis=parse_thesis(self.provider, text),
            provider=self.provider,
            model=self.model,
            input_tokens=_integer(usage_map.get("input_tokens")),
            output_tokens=_integer(usage_map.get("output_tokens")),
        )


def _content_text(content: object) -> str | None:
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                return text
    return None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
