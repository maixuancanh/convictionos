import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.trading import TradeIntent
from convictionos.infrastructure.alpaca import AlpacaRestOrderReader
from convictionos.infrastructure.brokers import (
    BrokerOrder,
    BrokerRejected,
    UnknownSubmission,
)


@dataclass(frozen=True)
class AlpacaCliConfig:
    executable: str
    revision: str
    api_key_id: str
    api_secret_key: str
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class AlpacaCliResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    possible_submission: bool


@dataclass(frozen=True)
class AlpacaCliAuditRecord:
    revision: str
    binary_sha256: str
    argv_fingerprint: str
    payload_hash: str
    started_at: datetime
    ended_at: datetime
    exit_classification: str


CliRunner = Callable[[list[str], str, dict[str, str], float], Awaitable[AlpacaCliResult]]


class AlpacaCliOrderSubmitter:
    def __init__(self, *, config: AlpacaCliConfig, runner: CliRunner | None = None) -> None:
        self._config = config
        self._runner = runner or run_alpaca_cli
        self.audit_records: list[AlpacaCliAuditRecord] = []

    async def submit(self, intent: TradeIntent) -> BrokerOrder:
        argv = [self._config.executable, "api", "POST", "/v2/orders", "--quiet"]
        payload = build_mleg_order_payload(intent)
        stdin = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        env = {
            "ALPACA_API_KEY": self._config.api_key_id,
            "ALPACA_SECRET_KEY": self._config.api_secret_key,
            "ALPACA_LIVE_TRADE": "false",
            "ALPACA_QUIET": "true",
        }
        started_at = datetime.now(UTC)
        try:
            result = await self._runner(argv, stdin, env, self._config.timeout_seconds)
        except OSError as error:
            self._record(argv, stdin, started_at, "process_start_failed")
            raise UnknownSubmission("Alpaca CLI process could not start") from error
        if result.timed_out or result.possible_submission:
            self._record(argv, stdin, started_at, "timeout_unknown")
            raise UnknownSubmission(intent.idempotency_key)
        if result.exit_code in {1, 2}:
            self._record(argv, stdin, started_at, "definitive_reject")
            raise BrokerRejected(_sanitized_cli_message(result.stderr))
        if result.exit_code != 0:
            self._record(argv, stdin, started_at, "nonzero_unknown")
            raise UnknownSubmission(intent.idempotency_key)
        try:
            order = AlpacaRestOrderReader._map_order(json.loads(result.stdout))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self._record(argv, stdin, started_at, "malformed_response")
            raise UnknownSubmission(intent.idempotency_key) from error
        if order.client_order_id != intent.idempotency_key:
            self._record(argv, stdin, started_at, "identity_mismatch")
            raise UnknownSubmission("broker response client order id does not match intent")
        self._record(argv, stdin, started_at, "accepted")
        return order

    def _record(
        self, argv: list[str], stdin: str, started_at: datetime, exit_classification: str
    ) -> None:
        self.audit_records.append(
            AlpacaCliAuditRecord(
                revision=self._config.revision,
                binary_sha256=_binary_sha256(self._config.executable),
                argv_fingerprint=sha256_hex(argv),
                payload_hash=sha256_hex(stdin),
                started_at=started_at,
                ended_at=datetime.now(UTC),
                exit_classification=exit_classification,
            )
        )


def build_mleg_order_payload(intent: TradeIntent) -> dict[str, Any]:
    return {
        "order_class": "mleg",
        "qty": str(intent.legs[0].quantity),
        "type": intent.order_type.value,
        "time_in_force": "day",
        "limit_price": str(intent.limit_price),
        "client_order_id": intent.idempotency_key,
        "legs": [
            {
                "symbol": leg.symbol,
                "ratio_qty": str(leg.ratio_quantity),
                "side": leg.side.value,
                "position_intent": leg.position_intent.value,
            }
            for leg in intent.legs
        ],
    }


async def run_alpaca_cli(
    argv: list[str], stdin: str, env: dict[str, str], timeout_seconds: float
) -> AlpacaCliResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return AlpacaCliResult(124, "", "timeout", timed_out=True, possible_submission=True)
    return AlpacaCliResult(
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
        timed_out=False,
        possible_submission=False,
    )


def _sanitized_cli_message(stderr: str) -> str:
    try:
        payload = json.loads(stderr)
    except json.JSONDecodeError:
        return "Alpaca CLI rejected the order"
    message = payload.get("message")
    if isinstance(message, str) and message:
        return message
    return "Alpaca CLI rejected the order"


def _binary_sha256(executable: str) -> str:
    path = Path(executable)
    if not path.is_file():
        return "unavailable"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
