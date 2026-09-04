from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORBIDDEN_WORDING = re.compile(
    r"(?i)\b(guaranteed|profitable|live trading|fully autonomous)\b"
)

REQUIRED_SUBMISSION_FILES = [
    "submission/one-page.md",
    "submission/project-description.md",
    "submission/technology.md",
    "submission/business-value.md",
    "submission/demo-script.md",
    "submission/video-script.md",
    "submission/slides/outline.md",
    "submission/social/x-post.md",
    "submission/social/linkedin-post.md",
    "submission/assets/README.md",
]


def build_submission_manifest(
    root: Path, *, release_sha: str
) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        failures.append("release_sha must be a 40-character git SHA")

    ledger_path = root / "submission" / "claim-ledger.yaml"
    if not ledger_path.exists():
        failures.append("submission/claim-ledger.yaml is required")
        ledger: dict[str, Any] = {}
    else:
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            failures.append(f"submission/claim-ledger.yaml must be JSON-compatible YAML: {error}")
            ledger = {}

    files: dict[str, dict[str, Any]] = {}
    for relative_path in REQUIRED_SUBMISSION_FILES:
        path = root / relative_path
        if not path.exists():
            failures.append(f"{relative_path} is required")
            continue
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN_WORDING.search(text):
            failures.append(f"{relative_path} contains forbidden wording")
        files[relative_path] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "words": len(text.split()),
        }

    claims = ledger.get("claims", [])
    if not isinstance(claims, list) or not claims:
        failures.append("claim ledger must contain claims")

    event_requirements = ledger.get("event_requirements", {})
    if not isinstance(event_requirements, dict):
        failures.append("claim ledger must contain event_requirements")
        event_requirements = {}

    if failures:
        return None, failures

    return (
        {
            "release_sha": release_sha,
            "generated_at": datetime.now(UTC).isoformat(),
            "event_requirements": event_requirements,
            "claims": claims,
            "files": files,
        },
        [],
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args()

    manifest, failures = build_submission_manifest(args.root, release_sha=args.release_sha)
    if failures:
        raise SystemExit("\n".join(failures))
    assert manifest is not None
    output_path = args.root / "submission" / "dist" / "submission-manifest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"submission manifest written to {output_path}")


if __name__ == "__main__":
    main()
