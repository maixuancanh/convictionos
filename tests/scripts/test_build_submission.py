import json
from pathlib import Path

from scripts.build_submission import REQUIRED_SUBMISSION_FILES, build_submission_manifest


def _write_submission_file(root: Path, relative_path: str, text: str = "paper-only") -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_minimal_submission(root: Path) -> None:
    for relative_path in REQUIRED_SUBMISSION_FILES:
        _write_submission_file(root, relative_path, "ConvictionOS paper-only Alpaca options agent.")
    _write_submission_file(
        root,
        "submission/claim-ledger.yaml",
        json.dumps(
            {
                "release_sha": "unknown",
                "event_requirements": {
                    "source_url": "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon",
                    "date_window": "28 August-4 September 2026",
                    "paper_account_starting_balance": "$100,000",
                    "requires_options_trading": True,
                    "requires_one_page_writeup": True,
                    "form_fields_verified": False,
                    "form_verification_note": (
                        "Authenticated form fields must be verified before submit."
                    ),
                },
                "claims": [
                    {
                        "category": "agent",
                        "text": "ConvictionOS is a paper-only autonomous options agent.",
                        "evidence": [
                            {
                                "type": "test",
                                "locator": "uv run pytest -q",
                                "status": "verified",
                            }
                        ],
                        "status": "verified",
                    }
                ],
            }
        ),
    )


def test_build_submission_manifest_requires_all_package_files(tmp_path: Path) -> None:
    _write_submission_file(tmp_path, "submission/one-page.md")

    manifest, failures = build_submission_manifest(tmp_path, release_sha="f" * 40)

    assert manifest is None
    assert any("submission/project-description.md is required" in failure for failure in failures)


def test_build_submission_manifest_records_hashes_and_release_sha(tmp_path: Path) -> None:
    _write_minimal_submission(tmp_path)

    manifest, failures = build_submission_manifest(tmp_path, release_sha="f" * 40)

    assert failures == []
    assert manifest is not None
    assert manifest["release_sha"] == "f" * 40
    assert manifest["event_requirements"]["requires_options_trading"] is True
    assert manifest["files"]["submission/one-page.md"]["sha256"]
    assert manifest["claims"][0]["status"] == "verified"


def test_build_submission_manifest_rejects_overstated_copy(tmp_path: Path) -> None:
    _write_minimal_submission(tmp_path)
    (tmp_path / "submission" / "project-description.md").write_text(
        "Guaranteed profitable live trading for everyone.",
        encoding="utf-8",
    )

    manifest, failures = build_submission_manifest(tmp_path, release_sha="f" * 40)

    assert manifest is None
    assert any("forbidden wording" in failure for failure in failures)
