from pydantic import BaseModel, ConfigDict, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.closed_accounting import ClosedPositionAccounting
from convictionos.domain.mandates import PolicyOutcome
from convictionos.domain.market_data import MarketDataCapability
from convictionos.domain.paper_accounting import PaperExecutionAccounting


class DecisionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    intent_id: str
    operation_hash: str
    evidence_snapshot_hash: str
    mandate_id: str
    mandate_version: int
    policy_outcome: PolicyOutcome
    policy_reasons: tuple[str, ...]
    broker_order_id: str
    broker_status: str
    previous_receipt_hash: str
    market_data: MarketDataCapability | None = None
    paper_accounting: PaperExecutionAccounting | None = None
    position_id: str | None = None
    exit_decision_hash: str | None = None
    closed_accounting: ClosedPositionAccounting | None = None

    @model_validator(mode="after")
    def validate_accounting_provenance(self) -> "DecisionReceipt":
        if self.paper_accounting is not None and (
            self.market_data is None
            or self.paper_accounting.capability != self.market_data
        ):
            raise ValueError("paper accounting capability must match receipt market data")
        if self.closed_accounting is not None and self.position_id is None:
            raise ValueError("closed accounting requires a close position reference")
        if self.closed_accounting is not None and self.exit_decision_hash is None:
            raise ValueError("closed accounting requires an exit decision reference")
        if self.closed_accounting is not None and (
            self.market_data is None
            or self.closed_accounting.capability != self.market_data
        ):
            raise ValueError("closed accounting capability must match receipt market data")
        return self

    def receipt_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="python", exclude_none=True))
