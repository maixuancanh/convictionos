from typing import Any

import httpx

from convictionos.application.model_gateway import (
    IntelligenceRequest,
    ModelResult,
)
from convictionos.infrastructure.model_http import (
    SYSTEM_POLICY,
    ModelHTTP,
    evidence_prompt,
    parse_thesis,
    thesis_schema,
)


class OpenAIModelGateway(ModelHTTP):
    provider = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(http_client=http_client)
        self.model = model
        self._api_key = api_key

    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        payload: dict[str, object] = {
            "model": self.model,
            "instructions": SYSTEM_POLICY,
            "input": evidence_prompt(request),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "grounded_thesis",
                    "strict": True,
                    "schema": thesis_schema(),
                }
            },
        }
        response = await self._post_json(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload=payload,
        )
        text = _output_text(response.get("output"))
        if text is None:
            from convictionos.application.model_gateway import IntelligenceUnavailable

            raise IntelligenceUnavailable("openai refusal or missing output")
        usage = response.get("usage")
        usage_map = usage if isinstance(usage, dict) else {}
        return ModelResult(
            thesis=parse_thesis(self.provider, text),
            provider=self.provider,
            model=self.model,
            input_tokens=_integer(usage_map.get("input_tokens")),
            output_tokens=_integer(usage_map.get("output_tokens")),
        )


def _output_text(output: object) -> str | None:
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "output_text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    return text
    return None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
