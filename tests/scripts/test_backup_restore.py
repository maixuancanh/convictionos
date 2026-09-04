from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_backup_script_uses_safe_pg_dump_flags_and_never_prints_database_url() -> None:
    script = (ROOT / "scripts" / "backup_postgres.ps1").read_text()

    assert "pg_dump" in script
    assert "--format=custom" in script
    assert "--no-owner" in script
    assert "--no-acl" in script
    assert "DATABASE_URL" not in script
    assert "Write-Output $ConnectionString" not in script


def test_restore_script_requires_explicit_target_confirmation() -> None:
    script = (ROOT / "scripts" / "restore_postgres.ps1").read_text()

    assert "ConfirmRestoreTarget" in script
    assert "TargetDatabase" in script
    assert "throw \"ConfirmRestoreTarget must equal TargetDatabase\"" in script
    assert "dropdb" not in script.lower()


def test_verify_restore_script_supports_mutation_disabled_boot_check() -> None:
    script = (ROOT / "scripts" / "verify_restore.py").read_text()

    assert "--mutation-disabled" in script
    assert "alembic_version" in script
    assert "runtime_controls" in script
