from collections.abc import Coroutine
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Iterable, Optional

from aiosqlite.context import contextmanager
from aiosqlite import Connection, Cursor, Row

from .model import Filter, Guild, Whitelist
from .store import Storage


__all__ = ["SQLiteStorage"]


class _ExtendedConnection:
    __slots__ = ("_connection",)

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @contextmanager
    def execute(
        self, sql: str, parameters: Optional[Iterable[Any]] = None
    ) -> Coroutine[None, None, Cursor]:
        return self._connection.execute(sql, parameters)

    @asynccontextmanager
    async def execute_commited(
        self,
        sql: str,
        parameters: Optional[Iterable[Any]] = None,
    ) -> AsyncGenerator[Cursor, None]:
        async with self._connection.execute(sql, parameters) as cursor:
            yield cursor

        await self._connection.commit()

    async def execute_auto_closable(
        self, sql: str, parameters: Optional[Iterable[Any]] = None
    ) -> None:
        async with self._connection.execute(sql, parameters):
            pass

    async def execute_auto_closable_commited(
        self, sql: str, parameters: Optional[Iterable[Any]] = None
    ) -> None:
        await self.execute_auto_closable(sql, parameters)

        await self._connection.commit()


class SQLiteStorage(Storage):
    __slots__ = ("_connection",)

    def __init__(self, connection: Connection) -> None:
        self._connection = _ExtendedConnection(connection)

    async def prepare(self) -> None:
        await self._connection.execute_auto_closable("PRAGMA foreign_keys = ON")

    async def get_guild(self, guild_id: int) -> Guild:
        async with self._connection.execute(
            """
            SELECT staff_role_id, filter, volume, auto_leave, shuffle, loop
            FROM guilds
            WHERE id = ?
        """,
            (guild_id,),
        ) as cursor:
            row: Row = await cursor.fetchone()  # pyright: ignore

        return Guild(
            id=guild_id,
            staff_role_id=row[0],
            filter=Filter(row[1]),
            volume=row[2],
            auto_leave=bool(row[3]),
            shuffle=bool(row[4]),
            loop=bool(row[5]),
        )

    async def create_guild(self, guild_id: int) -> Guild:
        await self._connection.execute_auto_closable_commited(
            """
            INSERT INTO guilds (id)
            VALUES (?)
            ON CONFLICT (id)
            DO NOTHING
        """,
            (guild_id,),
        )

        return await self.get_guild(guild_id)

    async def set_guild_staff_role_id(self, guild_id: int, staff_role_id: int) -> None:
        await self._connection.execute_auto_closable_commited(
            """
            INSERT INTO guilds (id, staff_role_id)
            VALUES (:id, :staff_role_id)
            ON CONFLICT (id)
            DO UPDATE SET staff_role_id = :staff_role_id
        """,
            {
                "id": guild_id,
                "staff_role_id": staff_role_id,
            },
        )

    async def unset_guild_staff_role_id_if_same(
        self, guild_id: int, expected_staff_role_id: int
    ) -> None:
        await self._connection.execute_auto_closable_commited(
            """
            INSERT INTO guilds (id)
            VALUES (:id)
            ON CONFLICT (id)
            DO UPDATE SET staff_role_id =
                CASE WHEN staff_role_id = :expected_staff_role_id
                    THEN NULL
                    ELSE staff_role_id
                END
        """,
            {
                "id": guild_id,
                "expected_staff_role_id": expected_staff_role_id,
            },
        )

    async def set_guild_filter(self, guild_id: int, filter: Filter) -> None:
        await self._connection.execute_auto_closable_commited(
            """
            INSERT INTO guilds (id, filter)
            VALUES (:id, :filter)
            ON CONFLICT (id)
            DO UPDATE SET filter = :filter
        """,
            {"id": guild_id, "filter": filter.value},
        )

    async def set_guild_volume(self, guild_id: int, volume: int) -> None:
        await self._connection.execute_auto_closable_commited(
            """
            INSERT INTO guilds (id, volume)
            VALUES (:id, :volume)
            ON CONFLICT (id)
            DO UPDATE SET volume = :volume
        """,
            {"id": guild_id, "volume": volume},
        )

    async def set_guild_auto_leave(self, guild_id: int, auto_leave: bool) -> None:
        await self._connection.execute_auto_closable_commited(
            """
            INSERT INTO guilds (id, auto_leave)
            VALUES (:id, :auto_leave)
            ON CONFLICT (id)
            DO UPDATE SET auto_leave = :auto_leave
        """,
            {"id": guild_id, "auto_leave": int(auto_leave)},
        )

    async def switch_guild_shuffle(self, guild_id: int) -> bool:
        async with self._connection.execute(
            """
            INSERT INTO guilds (id)
            VALUES (?)
            ON CONFLICT (id)
            DO UPDATE SET shuffle = NOT shuffle
            RETURNING shuffle
        """,
            (guild_id,),
        ) as cursor:
            row: Row = await cursor.fetchone()  # pyright: ignore

        return bool(row[0])

    async def switch_guild_loop(self, guild_id: int) -> bool:
        async with self._connection.execute(
            """
            INSERT INTO guilds (id)
            VALUES (?)
            ON CONFLICT (id)
            DO UPDATE SET loop = NOT loop
            RETURNING loop
        """,
            (guild_id,),
        ) as cursor:
            row: Row = await cursor.fetchone()  # pyright: ignore

        return bool(row[0])

    async def get_whitelist(self) -> Whitelist:
        async with self._connection.execute("""
            SELECT guild_id FROM whitelist
        """) as cursor:
            guild_ids = set()
            async for row in cursor:
                guild_ids.add(row[0])
            return Whitelist(guild_ids)

    async def add_to_whitelist(self, guild_id: int) -> bool:
        async with self._connection.execute_commited(
            """
            INSERT INTO whitelist (guild_id)
            VALUES (?)
            ON CONFLICT (guild_id)
            DO NOTHING
            RETURNING TRUE
        """,
            (guild_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def remove_from_whitelist(self, guild_id: int) -> bool:
        async with self._connection.execute_commited(
            """
            DELETE FROM whitelist
            WHERE guild_id = ?
            RETURNING TRUE
        """,
            (guild_id,),
        ) as cursor:
            return await cursor.fetchone() is not None
