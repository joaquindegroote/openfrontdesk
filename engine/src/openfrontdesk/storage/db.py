import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from openfrontdesk.storage.models import Base

logger = logging.getLogger(__name__)

# One lock per conversation, by a 64-bit hash of its id.
_LOCK = text("SELECT pg_advisory_lock(hashtextextended(:id, 0))")
_UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))")


def _add_missing_columns(connection: Connection) -> None:
    """Give a database made by an earlier version the columns added since. `create_all` only
    makes missing tables. Every column added so far can be empty, so this is all the migration
    there is; a change that needs more than that needs a migration tool."""
    known = inspect(connection)
    only_if_missing = "IF NOT EXISTS " if connection.dialect.name == "postgresql" else ""
    for table in Base.metadata.sorted_tables:
        present = {column["name"] for column in known.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                kind = column.type.compile(connection.dialect)
                connection.execute(
                    text(
                        f"ALTER TABLE {table.name} ADD COLUMN {only_if_missing}{column.name} {kind}"
                    )
                )


class Database:
    def __init__(self, url: str) -> None:
        options: dict[str, Any] = {}
        if url.startswith("sqlite") and ":memory:" in url:
            # One shared connection, or every session would see its own empty database. It
            # can't hold two transactions at once, so it's for tests and evals, which run one
            # turn at a time.
            options = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        self.engine: AsyncEngine = create_async_engine(url, **options)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # Postgres only. A turn holds one of these connections from start to end, so they come
        # from their own pool: turns waiting for a conversation can't use up the connections
        # the queries need. Its size is how many turns one process runs or queues at a time.
        self._locks: AsyncEngine | None = None
        if url.startswith("postgresql"):
            self._locks = create_async_engine(
                url, isolation_level="AUTOCOMMIT", pool_size=10, max_overflow=40
            )

    async def create_all(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(_add_missing_columns)

    @asynccontextmanager
    async def conversation_lock(self, conversation_id: str) -> AsyncIterator[None]:
        """Hold a conversation across every engine process on the same Postgres, so two workers
        can't run its turns at once. Postgres drops the lock by itself if this process dies.
        On SQLite it does nothing: there is one process, and its own lock is enough."""
        if self._locks is None:
            yield
            return
        async with self._locks.connect() as connection:
            await connection.execute(_LOCK, {"id": conversation_id})
            try:
                yield
            finally:
                try:
                    await connection.execute(_UNLOCK, {"id": conversation_id})
                except Exception:
                    # Back in the pool it would keep the lock. Closed, Postgres releases it.
                    logger.warning("could not release a conversation lock", exc_info=True)
                    await connection.invalidate()

    async def dispose(self) -> None:
        await self.engine.dispose()
        if self._locks is not None:
            await self._locks.dispose()
