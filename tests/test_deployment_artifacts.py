from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_production_container_uses_migrations_healthcheck_and_port() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "python -m convictionos.commands api" in dockerfile
    assert "alembic upgrade head" not in dockerfile
    assert "--port ${PORT:-8000}" in dockerfile
    assert "urlopen('http://127.0.0.1:8000/health/live'" in dockerfile


def test_production_container_pins_alpaca_cli_build_stage() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM golang:1.24-bookworm" in dockerfile
    assert "ALPACA_CLI_REVISION=53606273aa230a40c64b783425dcb3f4423ede30" in dockerfile
    assert "git checkout ${ALPACA_CLI_REVISION}" in dockerfile
    assert "go build -o /out/alpaca ./cmd/alpaca" in dockerfile
    assert "COPY --from=alpaca-cli-builder /out/alpaca /usr/local/bin/alpaca" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "io.convictionos.alpaca-cli.revision" in dockerfile


def test_railway_config_has_healthcheck_and_restart_policy() -> None:
    railway = (ROOT / "railway.api.toml").read_text()
    default_railway = (ROOT / "railway.toml").read_text()

    assert 'healthcheckPath = "/health/live"' in railway
    assert 'healthcheckPath = "/health/live"' in default_railway
    assert 'restartPolicyType = "ON_FAILURE"' in railway
    assert "python -m convictionos.commands api" in railway


def test_railway_has_separate_worker_and_migration_roles() -> None:
    worker = (ROOT / "railway.worker.toml").read_text()
    migrate = (ROOT / "railway.migrate.toml").read_text()

    assert "python -m convictionos.commands worker" in worker
    assert "python -m convictionos.commands migrate" in migrate
    assert "healthcheckPath" not in migrate


def test_dockerignore_excludes_credentials_and_local_state() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert ".env" in dockerignore
    assert ".venv" in dockerignore
