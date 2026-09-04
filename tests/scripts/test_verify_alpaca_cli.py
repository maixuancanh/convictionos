import json
from pathlib import Path

import pytest

from scripts.verify_alpaca_cli import ProcessResult, verify_alpaca_cli


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> ProcessResult:
        self.calls.append(argv)
        if argv[-1] == "version":
            return ProcessResult(0, "alpaca version 53606273aa230a40c64b783425dcb3f4423ede30", "")
        if argv[-2:] == ["order", "submit"] or argv[-3:] == ["order", "submit", "--schema"]:
            return ProcessResult(0, '{"order_class":"mleg"}', "")
        if argv[-2:] == ["api", "--help"]:
            return ProcessResult(0, "alpaca api POST /v2/orders", "")
        raise AssertionError(argv)


def test_verify_alpaca_cli_returns_sanitized_offline_report(tmp_path: Path) -> None:
    binary = tmp_path / "alpaca"
    binary.write_bytes(b"fake alpaca binary")
    runner = FakeRunner()

    report = verify_alpaca_cli(
        binary=binary,
        expected_revision="53606273aa230a40c64b783425dcb3f4423ede30",
        offline=True,
        runner=runner,
        credential_markers=("paper-key", "paper-secret"),
    )

    payload = json.loads(report)
    assert payload["ok"] is True
    assert payload["revision"] == "53606273aa230a40c64b783425dcb3f4423ede30"
    assert len(payload["sha256"]) == 64
    assert payload["offline"] is True
    assert "paper-secret" not in report
    assert [binary.as_posix(), "version"] in runner.calls
    assert [binary.as_posix(), "order", "submit", "--schema"] in runner.calls
    assert [binary.as_posix(), "api", "--help"] in runner.calls


def test_verify_alpaca_cli_rejects_revision_mismatch(tmp_path: Path) -> None:
    binary = tmp_path / "alpaca"
    binary.write_bytes(b"fake alpaca binary")

    with pytest.raises(ValueError, match="revision"):
        verify_alpaca_cli(
            binary=binary,
            expected_revision="other-revision",
            offline=True,
            runner=FakeRunner(),
            credential_markers=(),
        )


def test_verify_alpaca_cli_fails_if_credentials_would_leak(tmp_path: Path) -> None:
    binary = tmp_path / "alpaca"
    binary.write_bytes(b"fake alpaca binary")

    class LeakyRunner(FakeRunner):
        def __call__(self, argv: list[str]) -> ProcessResult:
            if argv[-1] == "version":
                return ProcessResult(
                    0,
                    "alpaca version 53606273aa230a40c64b783425dcb3f4423ede30 paper-secret",
                    "",
                )
            return super().__call__(argv)

    with pytest.raises(ValueError, match="credential"):
        verify_alpaca_cli(
            binary=binary,
            expected_revision="53606273aa230a40c64b783425dcb3f4423ede30",
            offline=True,
            runner=LeakyRunner(),
            credential_markers=("paper-secret",),
        )
