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


class GeminiModelGateway(ModelHTTP):
    provider = "gemini"

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
            "systemInstruction": {"parts": [{"text": SYSTEM_POLICY}]},
            "contents": [{"role": "user", "parts": [{"text": evidence_prompt(request)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": thesis_schema(),
            },
        }
        response = await self._post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            payload=payload,
        )
        text = _candidate_text(response.get("candidates"))
        if text is None:
            raise IntelligenceUnavailable("gemini refusal or missing output")
        usage = response.get("usageMetadata")
        usage_map = usage if isinstance(usage, dict) else {}
        return ModelResult(
            thesis=parse_thesis(self.provider, text),
            provider=self.provider,
            model=self.model,
            input_tokens=_integer(usage_map.get("promptTokenCount")),
            output_tokens=_integer(usage_map.get("candidatesTokenCount")),
        )


def _candidate_text(candidates: object) -> str | None:
    if not isinstance(candidates, list) or not candidates:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return None
    content = candidate.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            return text
    return None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
