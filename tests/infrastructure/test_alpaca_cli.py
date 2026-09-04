from dataclasses import dataclass

import pytest

from convictionos.domain.trading import PositionIntent, Side
from convictionos.infrastructure.alpaca_cli import (
    AlpacaCliConfig,
    AlpacaCliOrderSubmitter,
    AlpacaCliResult,
    build_mleg_order_payload,
)
from convictionos.infrastructure.brokers import (
    BrokerOrderStatus,
    BrokerRejected,
    UnknownSubmission,
)
from tests.domain.test_trade_intent import make_intent


@dataclass
class RecordingRunner:
    result: AlpacaCliResult
    calls: list[tuple[list[str], str, dict[str, str]]]

    async def __call__(
        self, argv: list[str], stdin: str, env: dict[str, str], timeout_seconds: float
    ) -> AlpacaCliResult:
        del timeout_seconds
        self.calls.append((argv, stdin, env))
        return self.result


def config() -> AlpacaCliConfig:
    return AlpacaCliConfig(
        executable="alpaca",
        revision="53606273aa230a40c64b783425dcb3f4423ede30",
        api_key_id="paper-key",
        api_secret_key="paper-secret",
        timeout_seconds=5,
    )


def test_builds_canonical_multileg_payload_without_credentials() -> None:
    intent = make_intent()

    payload = build_mleg_order_payload(intent)

    assert payload == {
        "order_class": "mleg",
        "qty": "1",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "1.25",
        "client_order_id": "intent-001-v1",
        "legs": [
            {
                "symbol": "SPY280120C00500000",
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
            {
                "symbol": "SPY280120C00510000",
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
        ],
    }
    assert "paper-secret" not in repr(payload)


def test_builds_closing_multileg_payload_without_changing_economics() -> None:
    intent = make_intent()
    close_intent = intent.model_copy(
        update={
            "legs": (
                intent.legs[0].model_copy(
                    update={
                        "side": Side.SELL,
                        "position_intent": PositionIntent.SELL_TO_CLOSE,
                    }
                ),
                intent.legs[1].model_copy(
                    update={
                        "side": Side.BUY,
                        "position_intent": PositionIntent.BUY_TO_CLOSE,
                    }
                ),
            )
        }
    )

    payload = build_mleg_order_payload(close_intent)

    assert payload["client_order_id"] == close_intent.idempotency_key
    assert payload["limit_price"] == str(close_intent.limit_price)
    assert payload["legs"][0]["position_intent"] == "sell_to_close"
    assert payload["legs"][1]["position_intent"] == "buy_to_close"


@pytest.mark.asyncio
async def test_cli_submitter_executes_only_alpaca_api_post_orders() -> None:
    runner = RecordingRunner(
        AlpacaCliResult(
            exit_code=0,
            stdout='{"id":"order-1","client_order_id":"intent-001-v1","status":"filled"}',
            stderr="",
            timed_out=False,
            possible_submission=False,
        ),
        [],
    )
    submitter = AlpacaCliOrderSubmitter(config=config(), runner=runner)

    order = await submitter.submit(make_intent())

    assert order.status is BrokerOrderStatus.FILLED
    argv, stdin, env = runner.calls[0]
    assert argv == ["alpaca", "api", "POST", "/v2/orders", "--quiet"]
    assert '"client_order_id":"intent-001-v1"' in stdin
    assert "paper-key" not in stdin
    assert "paper-secret" not in stdin
    assert env == {
        "ALPACA_API_KEY": "paper-key",
        "ALPACA_SECRET_KEY": "paper-secret",
        "ALPACA_LIVE_TRADE": "false",
        "ALPACA_QUIET": "true",
    }
    audit = submitter.audit_records[0]
    assert audit.revision == "53606273aa230a40c64b783425dcb3f4423ede30"
    assert audit.binary_sha256 == "unavailable"
    assert audit.started_at <= audit.ended_at
    assert audit.exit_classification == "accepted"
    assert "paper-secret" not in repr(audit)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [1, 2])
async def test_cli_definitive_failures_are_rejected(exit_code: int) -> None:
    runner = RecordingRunner(
        AlpacaCliResult(
            exit_code=exit_code,
            stdout="",
            stderr='{"message":"invalid order","secret":"paper-secret"}',
            timed_out=False,
            possible_submission=False,
        ),
        [],
    )

    with pytest.raises(BrokerRejected, match="invalid order"):
        await AlpacaCliOrderSubmitter(config=config(), runner=runner).submit(make_intent())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        AlpacaCliResult(0, "not-json", "", False, False),
        AlpacaCliResult(124, "", "timeout", True, False),
        AlpacaCliResult(124, "", "timeout after write", True, True),
    ],
)
async def test_cli_ambiguous_outcomes_are_unknown_submissions(
    result: AlpacaCliResult,
) -> None:
    runner = RecordingRunner(result, [])

    with pytest.raises(UnknownSubmission):
        await AlpacaCliOrderSubmitter(config=config(), runner=runner).submit(make_intent())


@pytest.mark.asyncio
async def test_cli_rejects_mismatched_order_identity() -> None:
    runner = RecordingRunner(
        AlpacaCliResult(
            0,
            '{"id":"order-1","client_order_id":"other-intent","status":"filled"}',
            "",
            False,
            False,
        ),
        [],
    )

    with pytest.raises(UnknownSubmission, match="client order id"):
        await AlpacaCliOrderSubmitter(config=config(), runner=runner).submit(make_intent())
