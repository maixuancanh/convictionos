import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
WEB = ROOT / "apps" / "web"


def test_web_app_has_vercel_static_build_contract() -> None:
    package = json.loads((WEB / "package.json").read_text())
    vercel = json.loads((WEB / "vercel.json").read_text())

    assert package["scripts"]["build"] == "node scripts/build.mjs"
    assert package["scripts"]["lint"] == "node scripts/check-web.mjs"
    assert vercel["outputDirectory"] == "dist"
    assert {rewrite["source"] for rewrite in vercel["rewrites"]} == {"/dashboard"}


def test_vercel_docs_point_to_standalone_web_package() -> None:
    operations = (ROOT / "docs" / "operations" / "vercel-web.md").read_text()
    infra = (ROOT / "infra" / "vercel" / "README.md").read_text()

    assert "Root directory: `apps/web`" in operations
    assert "Output directory: `dist`" in operations
    assert "NEXT_PUBLIC_API_BASE_URL" in infra
    assert "must never receive broker credentials" in infra


def test_landing_is_commercial_trust_first_not_technical_demo() -> None:
    landing = (WEB / "src" / "index.html").read_text()

    assert "Open Mission Control" in landing
    assert "AI can reason. Deterministic risk decides." in landing
    assert "Paper-only" in landing
    assert "No return promises" in landing


def test_dashboard_discloses_qualified_states_and_no_secret_inputs() -> None:
    dashboard = (WEB / "src" / "dashboard.html").read_text()
    script = (WEB / "src" / "dashboard.js").read_text()
    build_script = (WEB / "scripts" / "build.mjs").read_text()

    assert "Mission Control" in dashboard
    assert "P&amp;L claim" in dashboard
    assert "Unclaimed" in dashboard
    assert "release-sha" in dashboard
    assert "NEXT_PUBLIC_API_BASE_URL" in script
    assert "data.outcome" in script
    assert "RELEASE_SHA" in build_script
    assert "api key" not in f"{dashboard} {script}".lower()
    assert "secret" not in f"{dashboard} {script}".lower()
