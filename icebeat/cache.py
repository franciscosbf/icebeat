from typing import Optional

from cachetools import TTLCache

from .model import Guild, Whitelist
from .store import Cache

__all__ = ["TimedCache"]


class TimedCache(Cache):
    def __init__(self, entries: int, ttl: int) -> None:
        self.cache = TTLCache(entries, ttl)

    def get_guild(self, guild_id: int) -> Optional[Guild]:
        self.cache.get(guild_id, None)

    def set_guild(self, guild: Guild) -> None:
        self.cache[guild.id] = guild

    def get_whitelist(self) -> Optional[Whitelist]:
        self.cache.get("whitelist", None)

    def set_whitelist(self, whitelist: Whitelist) -> None:
        self.cache["whitelist"] = whitelist
