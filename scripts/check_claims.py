from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

FORBIDDEN_CLAIM = re.compile(
    r"(?i)\b(guaranteed|profitable|live trading|fully autonomous)\b"
)
ALLOWED_STATUSES = {"verified", "qualified", "unavailable", "prohibited"}
REQUIRED_EVENT_REQUIREMENTS = {
    "requires_options_trading": True,
    "paper_account_starting_balance": "$100,000",
    "requires_one_page_writeup": True,
}


def check_claims(
    root: Path,
    *,
    allow_missing_public_before_first_release: bool = False,
    submission_mode: bool = False,
) -> list[str]:
    del allow_missing_public_before_first_release
    path = root / "submission" / "claim-ledger.yaml"
    if not path.exists():
        return ["submission/claim-ledger.yaml is required"]
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"submission/claim-ledger.yaml must be JSON-compatible YAML: {error}"]
    failures: list[str] = []
    release_sha = str(ledger.get("release_sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}|unknown", release_sha):
        failures.append("release_sha must be a 40-character git SHA or unknown")
    if submission_mode:
        failures.extend(_check_event_requirements(ledger))
    claims = ledger.get("claims", [])
    if not isinstance(claims, list) or not claims:
        failures.append("at least one claim is required")
        return failures
    for index, claim in enumerate(claims, start=1):
        failures.extend(_check_claim(index, claim, submission_mode=submission_mode))
    return failures


def _check_event_requirements(ledger: dict[str, Any]) -> list[str]:
    requirements = ledger.get("event_requirements")
    if not isinstance(requirements, dict):
        return ["event_requirements are required in submission mode"]
    failures: list[str] = []
    for key, expected_value in REQUIRED_EVENT_REQUIREMENTS.items():
        if requirements.get(key) != expected_value:
            failures.append(f"event_requirements.{key} must be {expected_value!r}")
    if "source_url" not in requirements:
        failures.append("event_requirements.source_url is required")
    return failures


def _check_claim(index: int, claim: Any, *, submission_mode: bool = False) -> list[str]:
    if not isinstance(claim, dict):
        return [f"claim {index}: must be an object"]
    failures: list[str] = []
    text = str(claim.get("text", ""))
    evidence = claim.get("evidence", [])
    if not text:
        failures.append(f"claim {index}: text is required")
    if not isinstance(evidence, list) or not evidence:
        failures.append(f"claim {index}: evidence is required")
    if FORBIDDEN_CLAIM.search(text):
        failures.append(f"claim {index}: forbidden claim requires unavailable evidence")
    if submission_mode:
        status = str(claim.get("status", ""))
        if status not in ALLOWED_STATUSES:
            failures.append(f"claim {index}: status must be one of {sorted(ALLOWED_STATUSES)}")
        if not str(claim.get("category", "")):
            failures.append(f"claim {index}: category is required")
        for evidence_index, evidence_item in enumerate(evidence, start=1):
            if not isinstance(evidence_item, dict):
                failures.append(
                    f"claim {index} evidence {evidence_index}: structured evidence is required"
                )
                continue
            if not evidence_item.get("type"):
                failures.append(f"claim {index} evidence {evidence_index}: type is required")
            if not evidence_item.get("locator"):
                failures.append(f"claim {index} evidence {evidence_index}: locator is required")
            if evidence_item.get("status") not in ALLOWED_STATUSES:
                failures.append(
                    f"claim {index} evidence {evidence_index}: status must be one of "
                    f"{sorted(ALLOWED_STATUSES)}"
                )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--allow-missing-public-before-first-release", action="store_true")
    parser.add_argument("--submission-mode", action="store_true")
    args = parser.parse_args()
    failures = check_claims(
        args.root,
        allow_missing_public_before_first_release=args.allow_missing_public_before_first_release,
        submission_mode=args.submission_mode,
    )
    if failures:
        raise SystemExit("\n".join(failures))
    print("claims ok")


if __name__ == "__main__":
    main()
