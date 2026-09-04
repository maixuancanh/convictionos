import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from convictionos.infrastructure.models import Base

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    @pytest.hookimpl
    def pytest_asyncio_loop_factories(
        config: pytest.Config, item: pytest.Item
    ) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
        del config, item
        return {"selector": asyncio.SelectorEventLoop}


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://convictionos:convictionos@localhost:54330/convictionos_test",
)


def _set_search_path(schema_name: str) -> Callable[[Any, Any], None]:
    def handler(dbapi_connection: Any, connection_record: Any) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f'SET search_path TO "{schema_name}"')
        finally:
            cursor.close()

    return handler


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    schema_name = f"test_{uuid4().hex}"
    value = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with value.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    search_path_handler = _set_search_path(schema_name)
    event.listen(value.sync_engine, "connect", search_path_handler)

    async with value.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield value
    finally:
        async with value.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        event.remove(value.sync_engine, "connect", search_path_handler)
        await value.dispose()
