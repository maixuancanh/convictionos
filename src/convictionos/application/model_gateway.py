from typing import Protocol

from pydantic import BaseModel, ConfigDict

from convictionos.domain.intelligence import EvidenceBundle, GroundedThesis


class IntelligenceUnavailable(RuntimeError):
    """Raised when a model cannot produce a usable intelligence result."""


class IntelligenceRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence: EvidenceBundle
    prompt_version: str
    schema_version: str


class ModelResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    thesis: GroundedThesis
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelGateway(Protocol):
    provider: str
    model: str

    async def analyze(self, request: IntelligenceRequest) -> ModelResult: ...
