import argparse
import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PINNED_ALPACA_CLI_REVISION = "53606273aa230a40c64b783425dcb3f4423ede30"


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], ProcessResult]


def verify_alpaca_cli(
    *,
    binary: Path,
    expected_revision: str,
    offline: bool,
    runner: Runner | None = None,
    credential_markers: tuple[str, ...] = (),
) -> str:
    if runner is None:
        runner = _run_process
    if not binary.is_file():
        raise ValueError("Alpaca CLI binary does not exist")

    binary_arg = binary.as_posix()
    version = runner([binary_arg, "version"])
    schema = runner([binary_arg, "order", "submit", "--schema"])
    api_help = runner([binary_arg, "api", "--help"])
    results = (version, schema, api_help)
    _assert_success(results)
    _assert_no_credential_leak(results, credential_markers)

    version_text = f"{version.stdout}\n{version.stderr}"
    if expected_revision not in version_text:
        raise ValueError("Alpaca CLI reported revision does not match configured revision")

    report = {
        "ok": True,
        "offline": offline,
        "revision": expected_revision,
        "sha256": _sha256_file(binary),
        "checks": {
            "version": True,
            "order_submit_schema": True,
            "api_help": True,
        },
    }
    encoded = json.dumps(report, separators=(",", ":"), sort_keys=True)
    _assert_no_credential_text(encoded, credential_markers)
    return encoded


def _run_process(argv: list[str]) -> ProcessResult:
    completed = subprocess.run(argv, capture_output=True, check=False, text=True)
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def _assert_success(results: tuple[ProcessResult, ...]) -> None:
    for result in results:
        if result.returncode != 0:
            raise ValueError("Alpaca CLI verification command failed")


def _assert_no_credential_leak(
    results: tuple[ProcessResult, ...], credential_markers: tuple[str, ...]
) -> None:
    for result in results:
        _assert_no_credential_text(result.stdout, credential_markers)
        _assert_no_credential_text(result.stderr, credential_markers)


def _assert_no_credential_text(text: str, credential_markers: tuple[str, ...]) -> None:
    for marker in credential_markers:
        if marker and marker in text:
            raise ValueError("Alpaca CLI verification would leak a credential marker")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default="/usr/local/bin/alpaca")
    parser.add_argument("--expected-revision", default=PINNED_ALPACA_CLI_REVISION)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    report = verify_alpaca_cli(
        binary=Path(args.binary),
        expected_revision=args.expected_revision,
        offline=args.offline,
        credential_markers=(),
    )
    print(report)


if __name__ == "__main__":
    main()
