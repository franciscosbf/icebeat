from abc import ABC, abstractmethod
import dataclasses
from typing import Optional
from copy import copy

from .model import Filter, Guild, Whitelist

__all__ = ["Cache", "Storage", "Store"]


class Cache(ABC):
    @abstractmethod
    def get_guild(self, guild_id: int) -> Optional[Guild]: ...

    @abstractmethod
    def set_guild(self, guild: Guild) -> None: ...

    @abstractmethod
    def invalidate_guild(self, guild_id: int) -> None: ...

    @abstractmethod
    def get_whitelist(self) -> Optional[Whitelist]: ...

    @abstractmethod
    def set_whitelist(self, whitelist: Whitelist) -> None: ...

    @abstractmethod
    def invalidate_whitelist(self) -> None: ...


class Storage(ABC):
    @abstractmethod
    async def prepare(self) -> None: ...

    @abstractmethod
    async def get_guild(self, guild_id: int) -> Guild: ...

    @abstractmethod
    async def create_guild(self, guild_id: int) -> Guild: ...

    @abstractmethod
    async def set_guild_staff_role_id(
        self, guild_id: int, staff_role_id: int
    ) -> None: ...

    @abstractmethod
    async def unset_guild_staff_role_id_if_same(
        self, guild_id: int, expected_staff_role_id: int
    ) -> None: ...

    @abstractmethod
    async def set_guild_filter(self, guild_id: int, filter: Filter) -> None: ...

    @abstractmethod
    async def set_guild_volume(self, guild_id: int, volume: int) -> None: ...

    @abstractmethod
    async def set_guild_auto_leave(self, guild_id: int, auto_leave: bool) -> None: ...

    @abstractmethod
    async def switch_guild_shuffle(self, guild_id: int) -> bool: ...

    @abstractmethod
    async def switch_guild_loop(self, guild_id: int) -> bool: ...

    @abstractmethod
    async def get_whitelist(self) -> Whitelist: ...

    @abstractmethod
    async def add_to_whitelist(self, guild_id: int) -> bool: ...

    @abstractmethod
    async def remove_from_whitelist(self, guild_id: int) -> bool: ...


class Store:
    def __init__(self, cache: Cache, storage: Storage) -> None:
        self._cache = cache
        self._storage = storage

    async def get_guild(self, guild_id: int) -> Guild:
        guild = self._cache.get_guild(guild_id)

        if not guild:
            guild = await self._storage.create_guild(guild_id)
            self._cache.set_guild(guild)

        return dataclasses.replace(guild)

    async def prepare(self) -> None:
        await self._storage.prepare()

    async def create_guild(self, guild_id: int) -> None:
        await self.get_guild(guild_id)

    async def set_guild_staff_role_id(self, guild_id: int, staff_role_id: int) -> None:
        await self._storage.set_guild_staff_role_id(guild_id, staff_role_id)

        self._cache.invalidate_guild(guild_id)

    async def unset_guild_staff_role_id_if_same(
        self, guild_id: int, expected_staff_role_id: int
    ) -> None:
        await self._storage.unset_guild_staff_role_id_if_same(
            guild_id, expected_staff_role_id
        )

        self._cache.invalidate_guild(guild_id)

    async def set_guild_filter(self, guild_id: int, filter: Filter) -> None:
        await self._storage.set_guild_filter(guild_id, filter)

        self._cache.invalidate_guild(guild_id)

    async def set_guild_volume(self, guild_id: int, *, volume: int) -> None:
        await self._storage.set_guild_volume(guild_id, volume)

        self._cache.invalidate_guild(guild_id)

    async def set_guild_auto_leave(self, guild_id: int, *, auto_leave: bool) -> None:
        await self._storage.set_guild_auto_leave(guild_id, auto_leave)

        self._cache.invalidate_guild(guild_id)

    async def switch_guild_shuffle(self, guild_id: int) -> bool:
        shuffle = await self._storage.switch_guild_shuffle(guild_id)

        self._cache.invalidate_guild(guild_id)

        return shuffle

    async def switch_guild_loop(self, guild_id: int) -> bool:
        loop = await self._storage.switch_guild_loop(guild_id)

        self._cache.invalidate_guild(guild_id)

        return loop

    async def get_whitelist(self) -> Whitelist:
        whitelist = self._cache.get_whitelist()

        if not whitelist:
            whitelist = await self._storage.get_whitelist()
            self._cache.set_whitelist(whitelist)

        return Whitelist(copy(whitelist.guild_ids))

    async def add_to_whitelist(self, guild_id: int) -> bool:
        inserted = await self._storage.add_to_whitelist(guild_id)

        self._cache.invalidate_whitelist()

        return inserted

    async def remove_from_whitelist(self, guild_id: int) -> bool:
        removed = await self._storage.remove_from_whitelist(guild_id)

        self._cache.invalidate_whitelist()

        return removed
