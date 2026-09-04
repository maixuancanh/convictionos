from pathlib import Path

from scripts.check_architecture import check_architecture


def test_architecture_gate_accepts_current_repository() -> None:
    assert check_architecture(Path(__file__).parents[2]) == []


def test_architecture_gate_rejects_public_secret_schema_field(tmp_path: Path) -> None:
    (tmp_path / "src" / "convictionos" / "api" / "schemas").mkdir(parents=True)
    (tmp_path / "src" / "convictionos" / "api" / "schemas" / "bad.py").write_text(
        "class PublicThing:\n    api_secret: str\n"
    )

    failures = check_architecture(tmp_path)

    assert any("credential-like public schema field" in failure for failure in failures)
