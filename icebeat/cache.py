from typing import Any, Optional

from cachetools import TTLCache

from .model import Guild, Whitelist
from .store import Cache

__all__ = ["TimedCache"]

_DEFAULT_ENTRIES = 100
_DEFAULT_TTL = 3600


class TimedCache(Cache):
    __slots__ = ("_cache",)

    def __init__(
        self, entries: Optional[int] = None, ttl: Optional[int] = None
    ) -> None:
        if not entries:
            entries = _DEFAULT_ENTRIES
        if not ttl:
            ttl = _DEFAULT_TTL

        self._cache = TTLCache(entries, ttl)

    def _pop(self, key: Any) -> None:
        self._cache.pop(key, default=None)

    def get_guild(self, guild_id: int) -> Optional[Guild]:
        self._cache.get(guild_id, None)

    def set_guild(self, guild: Guild) -> None:
        self._cache[guild.id] = guild

    def invalidate_guild(self, guild_id: int) -> None:
        self._pop(guild_id)

    def get_whitelist(self) -> Optional[Whitelist]:
        self._cache.get("whitelist", None)

    def set_whitelist(self, whitelist: Whitelist) -> None:
        self._cache["whitelist"] = whitelist

    def invalidate_whitelist(self) -> None:
        self._pop("whitelist")
