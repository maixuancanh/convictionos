from convictionos.settings import Settings


def test_package_exposes_version() -> None:
    import convictionos

    assert convictionos.__version__ == "0.1.0"


def test_railway_postgres_url_uses_psycopg3_async_driver() -> None:
    settings = Settings(database_url="postgres://user:pass@postgres.railway.internal:5432/app")

    assert settings.database_url == (
        "postgresql+psycopg://user:pass@postgres.railway.internal:5432/app"
    )
