import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_live_paper_acceptance_read_only_writes_redacted_report(tmp_path: Path) -> None:
    report = tmp_path / "acceptance.json"
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/live_paper_acceptance.py",
            "--phase",
            "read-only",
            "--base-url",
            "https://example.invalid",
            "--output",
            str(report),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(report.read_text())

    assert payload["phase"] == "read-only"
    assert payload["order_mutations"] == 0
    assert payload["checks"]["health"] == "unavailable"
    assert "secret" not in result.stdout.lower()


def test_live_paper_acceptance_mutation_requires_all_explicit_guards(tmp_path: Path) -> None:
    report = tmp_path / "acceptance.json"
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/live_paper_acceptance.py",
            "--phase",
            "mutation",
            "--base-url",
            "https://example.invalid",
            "--output",
            str(report),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "authorize paper mutation" in result.stderr.lower()
    assert not report.exists()
