from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_public_release_docs_require_credential_rotation_and_clean_clone() -> None:
    rotation = (ROOT / "docs" / "security" / "credential-rotation.md").read_text()
    checklist = (ROOT / "docs" / "security" / "public-release-checklist.md").read_text()

    assert "revoke" in rotation.lower()
    assert "Google" in rotation
    assert "Alpaca" in rotation
    assert "OpenRouter" in rotation
    assert "fresh public clone" in checklist
    assert "info.txt" in checklist


def test_env_example_uses_non_secret_placeholders() -> None:
    env = (ROOT / ".env.example").read_text()

    assert "<paper secret>" not in env
    assert "<paper key" not in env
    assert "<long random server token>" not in env
    assert "replace-with-rotated-paper-key-id-in-secret-manager" in env
    assert "replace-with-rotated-paper-secret-in-secret-manager" in env
    assert "replace-with-random-control-token-in-secret-manager" in env


def test_readme_uses_rotated_secret_placeholders() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "<paper key" not in readme
    assert "<paper secret>" not in readme
    assert "<OpenRouter key>" not in readme
    assert "<long random server token>" not in readme
    assert "replace-with-rotated-paper-key-id-in-secret-manager" in readme
    assert "replace-with-rotated-openrouter-key-in-secret-manager" in readme


def test_readme_warns_about_rotating_exposed_credentials() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    single_line_readme = " ".join(readme.split())

    assert "chat, screenshots, `info.txt`, shell history" in readme
    assert "convictionos-worker" in readme
    assert "Only the API receives a public domain" in single_line_readme
