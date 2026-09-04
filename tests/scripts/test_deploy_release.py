import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_deploy_release_dry_run_records_order_without_secrets(tmp_path: Path) -> None:
    release_dir = tmp_path / "releases"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "deploy_release.ps1"),
            "-GitSha",
            "f" * 40,
            "-DryRun",
            "-ArtifactRoot",
            str(release_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    manifest = json.loads((release_dir / ("f" * 40) / "release.json").read_text())

    assert manifest["release_sha"] == "f" * 40
    assert manifest["dry_run"] is True
    assert manifest["steps"] == ["test", "migrate", "api", "worker", "verify"]
    assert "secret" not in result.stdout.lower()


def test_verify_deployment_accepts_secret_free_dry_run_manifest(tmp_path: Path) -> None:
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "release_sha": "f" * 40,
                "dry_run": True,
                "api_url": "https://example.invalid",
                "worker_public": False,
                "migration_public": False,
                "steps": ["test", "migrate", "api", "worker", "verify"],
            }
        )
    )

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/verify_deployment.py",
            "--release",
            str(release),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "deployment manifest ok" in result.stdout
