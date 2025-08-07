import logging
from typing import TYPE_CHECKING, Callable

from discord import (
    Client,
    Color,
    Embed,
    Interaction,
    Permissions,
    TextChannel,
    VoiceChannel,
    VoiceProtocol,
    app_commands,
)
from discord.types.voice import (
    GuildVoiceState as GuildVoiceStatePayload,
    VoiceServerUpdate as VoiceServerUpdatePayload,
)
from discord.ext import commands
import lavalink

from ..model import Filter

if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Music"]

__log__ = logging.getLogger(__name__)


_DEFAULT_PERMISSIONS = Permissions(
    connect=True,
    speak=True,
)


def _default_permissions() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    return app_commands.default_permissions(_DEFAULT_PERMISSIONS)


def _cooldown() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    return app_commands.checks.cooldown(
        rate=2, per=2.0, key=lambda interaction: interaction.guild_id
    )


class _GuildNotWhitelisted(app_commands.CheckFailure):
    pass


def _is_whitelisted() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    async def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]

        whitelist = await bot.store.get_whitelist()
        if interaction.guild_id in whitelist.guild_ids:  # pyright: ignore[reportOptionalMemberAccess]
            return True
        raise _GuildNotWhitelisted()

    return app_commands.check(predicate)


class _NotGuildOwner(app_commands.CheckFailure):
    pass


def _is_guild_owner() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    def predicate(interaction: Interaction) -> bool:
        if interaction.user.id == interaction.guild.owner_id:  # pyright: ignore[reportOptionalMemberAccess]
            return True
        raise _NotGuildOwner()

    return app_commands.check(predicate)


class _HasTextChannelSet(app_commands.CheckFailure):
    __slots__ = ("channel_id",)

    def __init__(self, channel_id: int) -> None:
        self.channel_id = channel_id


class _HasTextEnabledAndNotSet(app_commands.CheckFailure):
    pass


def _has_text_channel_set() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    async def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]

        guild = await bot.store.get_guild(interaction.guild_id)  # pyright: ignore[reportArgumentType]
        text_channel_id = guild.text_channel_id
        if guild.text_channel:
            if text_channel_id:
                if interaction.guild.get_channel(text_channel_id):  # pyright: ignore[reportOptionalMemberAccess]
                    if interaction.channel_id == text_channel_id:
                        return True
                    raise _HasTextChannelSet(text_channel_id)
                else:
                    await bot.store.unset_guild_text_channel_id(guild.id)
            raise _HasTextEnabledAndNotSet()
        return True

    return app_commands.check(predicate)


def _bot_has_permissions() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    return app_commands.checks.bot_has_permissions(
        connect=True,
        speak=True,
        send_messages=True,
    )


class _LavalinkVoiceClient(VoiceProtocol):
    __slots__ = ("_lavalink", "_destroyed", "_guild")

    def __init__(self, client: Client, channel: VoiceChannel) -> None:
        super().__init__(client, channel)

        self._lavalink: lavalink.Client = self.client.lavalink  # pyright: ignore[reportAttributeAccessIssue]
        self._destroyed = False
        self._guild = self.channel.guild

    async def _destroy(self) -> None:
        self.cleanup()

        if self._destroyed:
            return
        self._destroyed = True

        try:
            await self._lavalink.player_manager.destroy(self._guild.id)
        except lavalink.errors.ClientError:
            pass

    async def on_voice_state_update(self, data: GuildVoiceStatePayload) -> None:
        raw_channel_id = data["channel_id"]
        if not raw_channel_id:
            await self._destroy()

            return

        channel_id = int(raw_channel_id)
        self.channel: VoiceChannel = self.client.get_channel(channel_id)  # pyright: ignore[reportAttributeAccessIssue, reportIncompatibleVariableOverride]

        payload = {"t": "VOICE_STATE_UPDATE", "d": data}
        await self._lavalink.voice_update_handler(payload)

    async def on_voice_server_update(self, data: VoiceServerUpdatePayload) -> None:
        payload = {"t": "VOICE_SERVER_UPDATE", "d": data}
        await self._lavalink.voice_update_handler(payload)

    async def connect(
        self,
        *,
        timeout: float,
        reconnect: bool,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        _, _ = timeout, reconnect

        self._lavalink.player_manager.create(guild_id=self._guild.id)
        await self._guild.change_voice_state(
            channel=self.channel, self_mute=self_mute, self_deaf=self_deaf
        )

    async def disconnect(self, *, force: bool) -> None:
        player = self._lavalink.player_manager.get(self._guild.id)

        if not force and not player.is_connected:  # pyright: ignore[reportOptionalMemberAccess]
            return

        await self._guild.change_voice_state(channel=None)

        player.channel_id = None  #  pyright: ignore[reportOptionalMemberAccess]
        await self._destroy()


class Music(commands.Cog):
    __slots__ = ("_bot",)

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot

    @app_commands.command(description="player whatever you want")
    @app_commands.describe(query="link or normal search as if you were on YouTube")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_has_text_channel_set()
    @_bot_has_permissions()
    @_cooldown()
    async def play(self, interaction: Interaction, query: str) -> None:
        _, _ = interaction, query
        pass  # TODO: implement

    @app_commands.command(description="stops the player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_has_text_channel_set()
    @_bot_has_permissions()
    @_cooldown()
    async def pause(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="resumes the player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_has_text_channel_set()
    @_bot_has_permissions()
    @_cooldown()
    async def resume(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="skips what's currently playing")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_has_text_channel_set()
    @_bot_has_permissions()
    @_cooldown()
    async def skip(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="displays queue")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_has_text_channel_set()
    @_bot_has_permissions()
    @_cooldown()
    async def queue(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="enables/disables shuffle mode")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_has_text_channel_set()
    @_bot_has_permissions()
    @_cooldown()
    async def shuffle(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="change volume")
    @app_commands.describe(level="volume level (the higher, the worst)")
    @app_commands.guild_only()
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def volume(
        self, interaction: Interaction, level: app_commands.Range[int, 0, 1000]
    ) -> None:
        _, _ = interaction, level
        pass  # TODO: implement

    @app_commands.command(description="sets player filter")
    @app_commands.describe(name="filter name")
    @app_commands.guild_only()
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def filter(self, interaction: Interaction, name: Filter) -> None:
        _, _ = interaction, name
        pass  # TODO: implement

    _presence_group = app_commands.Group(
        name="presence",
        description="decide bot behaviour when queue is empty",
        guild_only=True,
    )

    @_presence_group.command(
        name="stay",
        description="bot won't leave the voice channel if the queue is empty",
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def presence_stay(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        guild = await self._bot.store.get_guild(guild_id)
        embed = Embed(color=Color.green())
        if not guild.auto_leave:
            embed.title = "Stay mode is already set"
        else:
            await self._bot.store.set_guild_auto_leave(guild_id, auto_leave=False)

            embed.title = "Stay mode has been activated"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_presence_group.command(
        name="leave",
        description="bot will remain in the voice channel if the queue is empty",
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def presence_leave(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        guild = await self._bot.store.get_guild(guild_id)
        embed = Embed(color=Color.green())
        if guild.auto_leave:
            embed.title = "Leave mode is already set"
        else:
            await self._bot.store.set_guild_auto_leave(guild_id, auto_leave=True)

            embed.title = "Leave mode has been activated"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    _search_group = app_commands.Group(
        name="search",
        description="select search mode",
        guild_only=True,
    )

    @_search_group.command(
        name="auto",
        description="if a normal search is provided, the bot will select the first result",
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def search_auto(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @_search_group.command(
        name="select",
        description="if a normal search is provided, you will be able to select between multiple results",
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def search_select(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    _channel_group = app_commands.Group(
        name="channel",
        description="manage text channel to send commands",
        guild_only=True,
    )

    @_channel_group.command(
        name="enable", description="enable exclusive text channel mode, if set"
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def channel_enable(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        guild = await self._bot.store.get_guild(guild_id)
        if guild.text_channel:
            embed = Embed(
                title="Exclusive text channel is already enabled", color=Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self._bot.store.set_guild_text_channel(
            guild_id,
            text_channel=True,
        )

        embed = Embed(
            title="Exclusive text channel mode has been enabled", color=Color.green()
        )
        guild = await self._bot.store.get_guild(guild_id)
        if not guild.text_channel_id:
            embed.set_footer(text="Note: do not forget to set a text channel")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_channel_group.command(
        name="disable", description="disable exclusive text channel mode, if set"
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def channel_disable(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        guild = await self._bot.store.get_guild(guild_id)
        if not guild.text_channel:
            embed = Embed(
                title="Exclusive text channel is already disabled", color=Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self._bot.store.set_guild_text_channel(
            guild_id,  # pyright: ignore[reportArgumentType]
            text_channel=False,
        )

        embed = Embed(
            title="Exclusive text channel mode has been disabled", color=Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_channel_group.command(name="set", description="set text channel mode")
    @app_commands.describe(channel="text channel")
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def channel_set(self, interaction: Interaction, channel: TextChannel) -> None:
        await self._bot.store.set_guild_text_channel_id(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            channel.id,
        )

        embed = Embed(
            title="Exclusive text channel was changed",
            description=f"From now on, commands will only be accepted in <#{channel.id}>",
        )
        embed.set_footer(text="Commands: play, pause, resume, skip, queue and shuffle")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="displays current player configuration")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_cooldown()
    async def info(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        guild = await self._bot.store.get_guild(guild_id)  # pyright: ignore[reportArgumentType]

        embed = Embed(
            title="Player Info:",
            color=Color.green(),
        )
        state = "enabled" if guild.text_channel else "disabled"
        text_channel = "not set"
        if guild.text_channel_id:
            if not interaction.guild.get_channel(guild.text_channel_id):  # pyright: ignore[reportOptionalMemberAccess]
                await self._bot.store.unset_guild_text_channel_id(guild_id)
            else:
                text_channel = f"<#{guild.text_channel_id}>"
        presence = "leave" if guild.auto_leave else "stay"
        embed.description = (
            f"**Text Channel:** {state}, {text_channel}\n"
            f"**Filter:** {guild.filter.name}\n"
            f"**Volume:** {guild.volume}\n"
            f"**When Queue Ends:** {presence}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, _GuildNotWhitelisted):
            embed = Embed(
                title="Server isn't whitelisted",
                color=Color.yellow(),
            )
        elif isinstance(error, _NotGuildOwner):
            embed = Embed(
                title="Only the server owner is allowed to execute this command",
                color=Color.yellow(),
            )
        elif isinstance(error, _HasTextChannelSet):
            embed = Embed(
                title="Exclusive text channel is set",
                description=f"Hop onto <#{error.channel_id}> to communicate with me",
                color=Color.yellow(),
            )
        elif isinstance(error, _HasTextEnabledAndNotSet):
            embed = Embed(
                title="Exclusive text channel is enabled but not set",
                description="Please contact the server owner",
                color=Color.yellow(),
            )
        elif isinstance(error, app_commands.BotMissingPermissions):
            perms = [
                f"_{perm.replace('_', ' ').replace('guild', 'server')}_"
                for perm in error.missing_permissions
            ]
            nperms = len(perms)
            if nperms == 1:
                fmted_perms = perms[0]
            elif nperms == 2:
                fmted_perms = f"{perms[0]} and {perms[1]}"
            else:
                fmted_perms = f"{', '.join(perms[:-1])} and {perms[-1]}"
            embed = Embed(
                title="I lack some capabilities",
                description=f"**Missing permissions:** {fmted_perms}",
                color=Color.yellow(),
            )
        elif isinstance(error, app_commands.CommandOnCooldown):
            embed = Embed(
                title="Take it easy, do not spam commands",
                color=Color.yellow(),
            )
            embed.set_footer(text="You still have tomorrow")
        else:
            __log__.warning(
                f"Error on {interaction.command.name} command",  # pyright: ignore[reportOptionalMemberAccess]
                exc_info=True,
            )

            embed = Embed(
                title="Something unexpected went wrong...",
                color=Color.red(),
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)
