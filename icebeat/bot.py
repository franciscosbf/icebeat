import logging
from typing import Any

from discord import Game, Guild, Intents, MemberCacheFlags, Object, Status
import discord
from discord.ext import commands
import lavalink

from .store import Store
from .cogs import Owner, Music

__all__ = ["IceBeat"]


_PREFIX = "!"
_DESCRIPTION = "IceBeat, a sort of jukebox"
_STATUS = Status.online
_ACTIVITY = Game(name="music")
_INTENTS = Intents(
    guilds=True, message_content=True, dm_messages=True, voice_states=True
)

__log__ = logging.getLogger(__name__)


class IceBeat(commands.Bot):
    __slots__ = ("store", "lavalink")

    def __init__(
        self,
        store: Store,
    ) -> None:
        super().__init__(
            command_prefix=_PREFIX,
            help_command=None,
            description=_DESCRIPTION,
            intents=_INTENTS,
            membed_cache_flags=MemberCacheFlags.from_intents(_INTENTS),
            status=_STATUS,
            activity=_ACTIVITY,
        )

        self.store = store

    async def _verify_whitelisted_guild(self, guild_id: int) -> None:
        try:
            await self.fetch_guild_preview(guild_id)
        except discord.NotFound:
            await self.store.remove_from_whitelist(guild_id)

            __log__.info(
                f"Servers {guild_id} was removed from whitelist as I couldn't find it"
            )

    async def setup_hook(self) -> None:
        await self.add_cog(Owner(self))

        for guild_id in (await self.store.get_whitelist()).guild_ids:
            await self._verify_whitelisted_guild(guild_id)

        whitelist = await self.store.get_whitelist()
        whitelisted_guilds = [Object(id=guild_id) for guild_id in whitelist.guild_ids]
        await self.add_cog(Music(self), guilds=whitelisted_guilds)

        for whitelisted_guild in whitelisted_guilds:
            await self.tree.sync(guild=whitelisted_guild)

    async def on_connect(self) -> None:
        __log__.info("Connected to Discord")

    async def on_disconnect(self) -> None:
        __log__.info("Disconnected from Discord")

    async def on_ready(self) -> None:
        __log__.info("I'm ready to serve")

    async def on_resumed(self) -> None:
        __log__.info("Session was resumed")

    async def on_guild_remove(self, guild: Guild) -> None:
        await self.store.remove_from_whitelist(guild.id)

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        _, _ = args, kwargs

        __log__.exception("Error raised by event %s", event_method)

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        _, _ = ctx, error

    async def run_forever(self, lavalink_client: lavalink.Client) -> None:
        self.lavalink_client = lavalink_client

        await self.connect(reconnect=True)
