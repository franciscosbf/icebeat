from typing import Any, Optional

from cachetools import TTLCache

from .model import Guild, Whitelist
from .store import Cache

__all__ = ["TimedCache"]


class TimedCache(Cache):
    def __init__(self, entries: int, ttl: int) -> None:
        self.cache = TTLCache(entries, ttl)

    def _pop(self, key: Any) -> None:
        self.cache.pop(key, default=None)

    def get_guild(self, guild_id: int) -> Optional[Guild]:
        self.cache.get(guild_id, None)

    def set_guild(self, guild: Guild) -> None:
        self.cache[guild.id] = guild

    def invalidate_guild(self, guild_id: int) -> None:
        self._pop(guild_id)

    def get_whitelist(self) -> Optional[Whitelist]:
        self.cache.get("whitelist", None)

    def set_whitelist(self, whitelist: Whitelist) -> None:
        self.cache["whitelist"] = whitelist

    def invalidate_whitelist(self) -> None:
        self._pop("whitelist")
