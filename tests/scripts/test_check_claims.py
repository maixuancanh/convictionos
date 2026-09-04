import json
from pathlib import Path

from scripts.check_claims import check_claims


def test_claim_gate_accepts_evidence_bounded_claim_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "submission" / "claim-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(
        json.dumps(
            {
                "release_sha": "f" * 40,
                "claims": [
                    {
                        "text": "Paper-only autonomous options agent with audit receipts.",
                        "evidence": ["tests"],
                    }
                ],
            }
        )
    )

    assert check_claims(tmp_path, allow_missing_public_before_first_release=True) == []


def test_claim_gate_rejects_forbidden_profit_claim_without_evidence(tmp_path: Path) -> None:
    ledger = tmp_path / "submission" / "claim-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(
        json.dumps(
            {
                "release_sha": "f" * 40,
                "claims": [{"text": "Guaranteed profitable live trading.", "evidence": []}],
            }
        )
    )

    failures = check_claims(tmp_path, allow_missing_public_before_first_release=True)

    assert any("forbidden claim" in failure for failure in failures)


def test_submission_mode_requires_structured_evidence_and_event_requirements(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "submission" / "claim-ledger.yaml"
    ledger.parent.mkdir()
    ledger.write_text(
        json.dumps(
            {
                "release_sha": "f" * 40,
                "event_requirements": {
                    "source_url": "https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon",
                    "requires_options_trading": True,
                    "paper_account_starting_balance": "$100,000",
                    "requires_one_page_writeup": True,
                    "form_fields_verified": False,
                },
                "claims": [
                    {
                        "category": "strategy",
                        "text": (
                            "ConvictionOS includes an options strategy "
                            "with deterministic risk gates."
                        ),
                        "status": "qualified",
                        "evidence": [
                            {
                                "type": "test",
                                "locator": "tests/application/test_strategy_router.py",
                                "status": "verified",
                            }
                        ],
                    }
                ],
            }
        )
    )

    assert check_claims(tmp_path, submission_mode=True) == []
