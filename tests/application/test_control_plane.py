from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.application.control_plane import ControlPlaneService
from convictionos.application.demo import DemoService
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.trading import IntentState, TradeIntent
from convictionos.infrastructure.brokers import FakeBroker
from convictionos.infrastructure.store import Store


class _ReceiptOverrideStore(Store):
    def __init__(self, delegate: Store, receipt: DecisionReceipt) -> None:
        self._delegate = delegate
        self._receipt = receipt

    async def find_receipt(self, intent_id: str) -> DecisionReceipt | None:
        if intent_id == self._receipt.intent_id:
            return self._receipt
        return await self._delegate.find_receipt(intent_id)

    async def load_intent(self, intent_id: str) -> TradeIntent:
        return await self._delegate.load_intent(intent_id)

    async def get_state(self, intent_id: str) -> IntentState:
        return await self._delegate.get_state(intent_id)


class _IntentOverrideStore(_ReceiptOverrideStore):
    def __init__(self, delegate: Store, receipt: DecisionReceipt, intent: TradeIntent) -> None:
        super().__init__(delegate, receipt)
        self._intent = intent

    async def load_intent(self, intent_id: str) -> TradeIntent:
        if intent_id == self._intent.intent_id:
            return self._intent
        return await self._delegate.load_intent(intent_id)


@pytest.mark.asyncio
async def test_summary_labels_fake_execution_and_latest_receipt(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    broker = FakeBroker()
    demo = DemoService(store, broker)
    await demo.run("SPY280120C00500000", "SPY280120C00510000", Decimal("1.25"))

    summary = await ControlPlaneService(store, broker_mode="fake").summary()

    assert summary.execution_mode == "FAKE DEMO · PAPER-SAFE"
    assert summary.live_trading_authorized is False
    assert summary.latest_decision is not None
    assert summary.latest_decision.receipt_hash is not None
    assert summary.latest_decision.intent_state == "reconciled"

    decision = summary.latest_decision
    assert decision.intent_id == "intent-synthetic-catalyst-001"
    assert decision.underlying == "SPY"
    assert decision.horizon == "catalyst"
    assert decision.thesis == (
        "A synthetic macro catalyst creates a bounded upside scenario for adapter verification "
        "only."
    )
    assert decision.max_loss == Decimal("125.00")
    assert decision.policy_outcome == "allow"
    assert decision.policy_reasons == ()
    assert decision.broker_order_id == "fake-intent-synthetic-catalyst-001"
    assert decision.evidence_snapshot_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_summary_loads_repo_fixture_when_called_from_another_cwd(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.chdir(tmp_path)

    summary = await ControlPlaneService(store, broker_mode="fake").summary()

    assert summary.case_id == "synthetic-catalyst-001"


@pytest.mark.asyncio
async def test_summary_rejects_receipt_with_mismatched_operation_hash(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    receipt = await DemoService(store, FakeBroker()).run(
        "SPY280120C00500000", "SPY280120C00510000", Decimal("1.25")
    )
    tampered = receipt.model_copy(update={"operation_hash": "sha256:tampered-operation"})
    fake_store = _ReceiptOverrideStore(store, tampered)

    with pytest.raises(ValueError, match="^receipt operation hash mismatch$"):
        await ControlPlaneService(fake_store, broker_mode="fake").summary()


@pytest.mark.asyncio
async def test_summary_rejects_receipt_with_mismatched_evidence_snapshot_hash(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    receipt = await DemoService(store, FakeBroker()).run(
        "SPY280120C00500000", "SPY280120C00510000", Decimal("1.25")
    )
    tampered = receipt.model_copy(update={"evidence_snapshot_hash": "sha256:tampered-snapshot"})
    fake_store = _ReceiptOverrideStore(store, tampered)

    with pytest.raises(ValueError, match="^receipt evidence snapshot hash mismatch$"):
        await ControlPlaneService(fake_store, broker_mode="fake").summary()


@pytest.mark.asyncio
async def test_summary_rejects_intent_with_mismatched_evidence_snapshot_hash(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    receipt = await DemoService(store, FakeBroker()).run(
        "SPY280120C00500000", "SPY280120C00510000", Decimal("1.25")
    )
    intent = await store.load_intent(receipt.intent_id)
    tampered = intent.model_copy(update={"data_snapshot_hash": "sha256:tampered-intent"})
    matching_receipt = receipt.model_copy(update={"operation_hash": tampered.operation_hash()})
    fake_store = _IntentOverrideStore(store, matching_receipt, tampered)

    with pytest.raises(ValueError, match="^intent evidence snapshot hash mismatch$"):
        await ControlPlaneService(fake_store, broker_mode="fake").summary()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker_mode", "execution_mode"),
    [
        ("fake", "FAKE DEMO · PAPER-SAFE"),
        ("alpaca_paper", "ALPACA PAPER · NO LIVE CAPITAL"),
        ("other", "UNAVAILABLE"),
    ],
)
async def test_summary_without_receipt_is_empty_and_paper_safe(
    engine: AsyncEngine, broker_mode: str, execution_mode: str
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))

    summary = await ControlPlaneService(store, broker_mode=broker_mode).summary()

    assert summary.execution_mode == execution_mode
    assert summary.live_trading_authorized is False
    assert summary.latest_decision is None
    assert summary.case_id == "synthetic-catalyst-001"
    assert summary.synthetic is True
    assert summary.evidence_snapshot_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_view_models_are_immutable(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    summary = await ControlPlaneService(store, broker_mode="fake").summary()

    with pytest.raises(ValidationError):
        summary.execution_mode = "live"
