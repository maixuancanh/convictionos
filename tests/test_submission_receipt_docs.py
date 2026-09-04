from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_submission_receipt_starts_unsubmitted_and_secret_safe() -> None:
    receipt = (ROOT / "submission" / "submission-receipt.md").read_text()

    assert "Status: not submitted yet." in receipt
    assert "Do not store credentials" in receipt
    assert "Starting balance confirmed as $100,000" in receipt


def test_post_submission_runbook_requires_authenticated_form_verification() -> None:
    runbook = (ROOT / "docs" / "operations" / "post-submission.md").read_text()

    assert "authenticated" in runbook
    assert "Do not infer these from the public landing page" in runbook
    assert "uv run python scripts/check_claims.py --submission-mode" in runbook
    assert "uv run python scripts/build_submission.py --release-sha" in runbook
