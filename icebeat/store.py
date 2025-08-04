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
    def get_whitelist(self) -> Optional[Whitelist]: ...

    @abstractmethod
    def set_whitelist(self, whitelist: Whitelist) -> None: ...


class Storage(ABC):
    @abstractmethod
    async def prepare(self) -> None: ...

    @abstractmethod
    async def get_guild(self, guild_id: int) -> Guild: ...

    @abstractmethod
    async def create_guild(self, guild_id: int) -> Guild: ...

    @abstractmethod
    async def set_guild_filter(self, guild_id: int, filter: Filter) -> None: ...

    @abstractmethod
    async def set_guild_volume(self, guild_id: int, volume: int) -> None: ...

    @abstractmethod
    async def set_guild_auto_leave(self, guild_id: int, auto_leave: bool) -> None: ...

    @abstractmethod
    async def set_optional_search(
        self, guild_id: int, optional_search: bool
    ) -> None: ...

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

    async def set_guild_filter(self, guild_id: int, filter: Filter) -> None:
        await self._storage.set_guild_filter(guild_id, filter)

        (await self.get_guild(guild_id)).filter = filter

    async def set_guild_volume(self, guild_id: int, volume: int) -> None:
        await self._storage.set_guild_volume(guild_id, volume)

        (await self.get_guild(guild_id)).volume = volume

    async def set_guild_auto_leave(self, guild_id: int, auto_leave: bool) -> None:
        await self._storage.set_guild_auto_leave(guild_id, auto_leave)

        (await self.get_guild(guild_id)).auto_leave = auto_leave

    async def get_whitelist(self) -> Whitelist:
        whitelist = self._cache.get_whitelist()

        if not whitelist:
            whitelist = await self._storage.get_whitelist()
            self._cache.set_whitelist(whitelist)

        return Whitelist(copy(whitelist.guild_ids))

    async def add_to_whitelist(self, guild_id: int) -> bool:
        inserted = await self._storage.add_to_whitelist(guild_id)

        (await self.get_whitelist()).guild_ids.add(guild_id)

        return inserted

    async def remove_from_whitelist(self, guild_id: int) -> bool:
        removed = await self._storage.remove_from_whitelist(guild_id)

        (await self.get_whitelist()).guild_ids.discard(guild_id)

        return removed
