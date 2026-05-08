import logging
from types import TracebackType
from typing import Any, Optional, Sequence, Type
from discord.utils import MISSING
from typing_extensions import override

from discord import (
    Activity,
    ActivityType,
    AllowedMentions,
    Guild,
    Intents,
    MemberCacheFlags,
    Object,
    Role,
    Status,
)
import discord
from discord.abc import Snowflake
from discord.ext import commands
import lavalink

from icebeat.config import Config
from icebeat.cooldown import CooldownPreset

from .store import Store
from .cogs import Owner, Music
from .treesync import (
    AppCommands,
    RegisteredAppCommands,
    TreeSyncEvents,
    RemovedAppCommands,
)

__all__ = [
    "IceBeat",
]

_PREFIX = "/"
_DEFAULT_DESCRIPTION = "IceBeat, a sort of jukebox"
_STATUS = Status.online
_DEFAULT_ACTIVITY_NAME = "music"
_INTENTS = Intents(guilds=True, dm_messages=True, voice_states=True)

__log__ = logging.getLogger(__name__)


class IceBeat(commands.Bot):
    __slots__ = (
        "cooldown_preset",
        "conf",
        "store",
        "lavalink_client",
        "_tree_sync_events",
    )

    def __init__(
        self,
        store: Store,
        conf: Config,
    ) -> None:
        description = (
            conf.bot.description if conf.bot.description else _DEFAULT_DESCRIPTION
        )
        activity = Activity(
            name=conf.bot.activity if conf.bot.activity else _DEFAULT_ACTIVITY_NAME,
            type=ActivityType.listening,
        )
        super().__init__(
            command_prefix=_PREFIX,
            help_command=None,
            description=description,
            intents=_INTENTS,
            member_cache_flags=MemberCacheFlags.from_intents(_INTENTS),
            status=_STATUS,
            activity=activity,
            allowed_mentions=AllowedMentions.none(),
        )

        self.cooldown_preset = CooldownPreset(
            conf.commands.cooldown_rate, conf.commands.cooldown_time
        )
        self.store = store
        self.conf = conf

        self.lavalink_client: lavalink.Client

        self._tree_sync_events = TreeSyncEvents()

    async def _sync_guild_app_commands(self, guild: Snowflake) -> None:
        commands = await self.tree.sync(guild=guild)

        self._tree_sync_events.dispatch(
            RegisteredAppCommands(guild, AppCommands(commands))
        )

    async def _verify_whitelisted_guilds(self) -> None:
        whitelist = await self.store.get_whitelist()

        for guild_id in whitelist.guild_ids:
            try:
                await self.fetch_guild_preview(guild_id)
            except discord.NotFound:
                await self.store.remove_from_whitelist(guild_id)

                __log__.info(
                    f"Server {guild_id} was removed from whitelist as I couldn't find it on Discord"
                )

        async for guild in self.fetch_guilds(limit=None):
            if guild.id not in whitelist.guild_ids:
                await self.remove_app_commands_from_guild(guild)

    async def _prepare_whitelisted_guilds(self) -> None:
        whitelist = await self.store.get_whitelist()
        whitelisted_guilds = [Object(id=guild_id) for guild_id in whitelist.guild_ids]

        await self.add_cog(Music(self), guilds=whitelisted_guilds)

        for whitelisted_guild in whitelisted_guilds:
            await self._sync_guild_app_commands(whitelisted_guild)

    async def _unload_cogs(self) -> None:
        for cog_name in list(self.cogs.keys()):
            await self.remove_cog(cog_name)

    @override
    async def add_cog(
        self,
        cog: commands.Cog,
        /,
        *,
        override: bool = False,
        guild: Optional[Snowflake] = MISSING,
        guilds: Sequence[Snowflake] = MISSING,
    ) -> None:
        await super().add_cog(cog, override=override, guild=guild, guilds=guilds)

        self._tree_sync_events.register_hooks(cog)

    @override
    async def setup_hook(self) -> None:
        await self.add_cog(Owner(self))

        await self._verify_whitelisted_guilds()

        await self._prepare_whitelisted_guilds()

    async def on_connect(self) -> None:
        __log__.info("Connected to Discord")

    async def on_disconnect(self) -> None:
        __log__.info("Disconnected from Discord")

    async def on_ready(self) -> None:
        __log__.info("I'm ready to serve")

    async def on_resumed(self) -> None:
        __log__.info("Session was resumed")

    async def on_guild_join(self, guild: Guild) -> None:
        await self.remove_app_commands_from_guild(guild)

    async def on_guild_remove(self, guild: Guild) -> None:
        await self.store.remove_from_whitelist(guild.id)

    async def on_guild_role_delete(self, role: Role) -> None:
        await self.store.unset_guild_staff_role_id_if_same(role.guild.id, role.id)

    @override
    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        _, _ = args, kwargs

        __log__.exception("Error raised by event %s", event_method)

    @override
    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        _, _ = ctx, error

    async def add_app_commands_to_guild(self, guild: Snowflake) -> None:
        for cog in self.cogs.values():
            commands = cog.get_app_commands()
            for command in commands:
                self.tree.add_command(command, guild=guild, override=True)
        await self._sync_guild_app_commands(guild)

    async def remove_app_commands_from_guild(self, guild: Snowflake) -> None:
        self._tree_sync_events.dispatch(RemovedAppCommands(guild))

        self.tree.clear_commands(guild=guild)
        await self.tree.sync(guild=guild)

        commands = await self.tree.fetch_commands(guild=guild)
        for command in commands:
            await self.http.delete_guild_command(self.client.id, guild.id, command.id)  # pyright: ignore[reportAttributeAccessIssue]

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        await super().__aexit__(exc_type, exc_value, traceback)

        await self._unload_cogs()
