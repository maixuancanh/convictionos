from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

EXPECTED_STEPS = ["test", "migrate", "api", "worker", "verify"]


def verify_release_manifest(path: Path) -> list[str]:
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        return [f"release manifest is invalid JSON: {error}"]
    failures: list[str] = []
    release_sha = str(manifest.get("release_sha", ""))
    if re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        failures.append("release_sha must be a 40-character git SHA")
    if manifest.get("steps") != EXPECTED_STEPS:
        failures.append("release steps must be test -> migrate -> api -> worker -> verify")
    if manifest.get("worker_public") is not False:
        failures.append("worker must not be public")
    if manifest.get("migration_public") is not False:
        failures.append("migration role must not be public")
    rendered = json.dumps(manifest, sort_keys=True).lower()
    if any(secret in rendered for secret in ("secret", "token", "password")):
        failures.append("release manifest contains credential-like text")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    args = parser.parse_args()
    failures = verify_release_manifest(args.release)
    if failures:
        raise SystemExit("\n".join(failures))
    print("deployment manifest ok")


if __name__ == "__main__":
    main()
