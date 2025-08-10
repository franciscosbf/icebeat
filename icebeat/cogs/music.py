import logging
import re
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


_URL_RE = re.compile(r"^http?://(?:www\.)?.+")
_DEFAULT_PERMISSIONS = Permissions(
    connect=True,
    use_application_commands=True,
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
    __slots__ = ("_lavalink_client", "_destroyed", "_guild")

    def __init__(self, client: Client, channel: VoiceChannel) -> None:
        super().__init__(client, channel)

        self._lavalink_client: lavalink.Client = self.client.lavalink_client  # pyright: ignore[reportAttributeAccessIssue]
        self._destroyed = False
        self._guild = self.channel.guild

    async def _destroy(self) -> None:
        self.cleanup()

        if self._destroyed:
            return
        self._destroyed = True

        try:
            await self._lavalink_client.player_manager.destroy(self._guild.id)
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
        await self._lavalink_client.voice_update_handler(payload)

    async def on_voice_server_update(self, data: VoiceServerUpdatePayload) -> None:
        payload = {"t": "VOICE_SERVER_UPDATE", "d": data}
        await self._lavalink_client.voice_update_handler(payload)

    async def connect(
        self,
        *,
        timeout: float,
        reconnect: bool,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        _, _ = timeout, reconnect

        self._lavalink_client.player_manager.create(guild_id=self._guild.id)
        await self._guild.change_voice_state(
            channel=self.channel, self_mute=self_mute, self_deaf=self_deaf
        )

    async def disconnect(self, *, force: bool) -> None:
        player = self._lavalink_client.player_manager.get(self._guild.id)

        if not force and not player.is_connected:  # pyright: ignore[reportOptionalMemberAccess]
            return

        await self._guild.change_voice_state(channel=None)

        player.channel_id = None  #  pyright: ignore[reportOptionalMemberAccess]
        await self._destroy()


class Music(commands.Cog):
    __slots__ = ("_bot", "_lavalink_client")

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot

        self._lavalink_client = self.setup_lavalink()
        self._bot.lavalink_client = self._lavalink_client

    def setup_lavalink(self) -> lavalink.Client:
        lavalink_client = lavalink.Client(self._bot.user.id)  # pyright: ignore reportOptionalMemberAccess

        lavalink_client.add_event_hooks(self)

        conf = self._bot.conf
        lavalink_client.add_node(
            conf.lavalink.host,
            conf.lavalink.port,
            conf.lavalink.password,
            conf.lavalink.region,
            conf.lavalink.name,
        )

        return lavalink_client

    async def cog_unload(self) -> None:
        await self._lavalink_client.close()

    async def _decide_bot_presence(self, guild_id: int) -> None:
        guild = self._bot.get_guild(guild_id)
        if not guild:
            return

        if (await self._bot.store.get_guild(guild_id)).auto_leave:
            await guild.voice_client.disconnect(force=True)  # pyright: ignore[reportOptionalMemberAccess]

    @lavalink.listener(lavalink.TrackStuckEvent)
    async def on_track_stuck(self, event: lavalink.TrackStuckEvent) -> None:
        __log__.debug(
            "Track %s got stuck in guild %d player",
            event.track.source_name,
            event.player.guild_id,
        )

    @lavalink.listener(lavalink.TrackExceptionEvent)
    async def on_track_exception(self, event: lavalink.TrackExceptionEvent) -> None:
        __log__.debug(
            "Track %s raised playback error on guild %d: %s",
            event.track.source_name,
            event.player.guild_id,
            event.cause,
        )

    @lavalink.listener(lavalink.TrackLoadFailedEvent)
    async def on_track_load_failed(self, event: lavalink.TrackLoadFailedEvent) -> None:
        __log__.debug(
            "Player has failed to load track %s in guild %d: ",
            event.track.source_name,
            event.player.guild_id,
            event.original or "track not playable",
        )

        player: lavalink.DefaultPlayer = event.player  # pyright: ignore[reportAssignmentType]
        try:
            await player.play()
        except lavalink.errors.RequestError:
            __log__.exception(
                "Encountered an error when trying to play next enqueued track on guild: %d",
                player.guild_id,
            )

            await self._decide_bot_presence(event.player.guild_id)

    @lavalink.listener(lavalink.QueueEndEvent)
    async def on_queue_end(self, event: lavalink.QueueEndEvent) -> None:
        await self._decide_bot_presence(event.player.guild_id)

    @lavalink.listener(lavalink.NodeConnectedEvent)
    async def on_node_connected(self, event: lavalink.NodeConnectedEvent) -> None:
        __log__.info("Successfully connected to Lavalink node %s", event.node.name)

    @lavalink.listener(lavalink.NodeDisconnectedEvent)
    async def on_node_disconnected(self, event: lavalink.NodeDisconnectedEvent) -> None:
        __log__.warning("Lavalink client disconnect from node %s", event.node.name)

    @lavalink.listener(lavalink.NodeReadyEvent)
    async def on_node_ready(self, event: lavalink.NodeReadyEvent) -> None:
        __log__.info("Lavalink node %s is ready to serve", event.node.name)

    @lavalink.listener(lavalink.PlayerErrorEvent)
    async def on_player_error(self, event: lavalink.PlayerErrorEvent) -> None:
        __log__.exception(
            "Guild %d player raised exception: %s",
            event.player.guild_id,
            event.original,
        )

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
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        player: lavalink.DefaultPlayer = self._bot.lavalink_client.player_manager.get(
            guild_id
        )  # pyright: ignore[reportAssignmentType]
        await player.set_volume(vol=level)

        await self._bot.store.set_guild_volume(guild_id, volume=level)

        embed = Embed(title="Volume has been changed")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        await self._bot.store.set_guild_auto_leave(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            auto_leave=False,
        )

        embed = Embed(title="Stay mode has been activated", color=Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_presence_group.command(
        name="leave",
        description="bot won't remain in the voice channel if the queue is empty",
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def presence_leave(self, interaction: Interaction) -> None:
        await self._bot.store.set_guild_auto_leave(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            auto_leave=True,
        )

        voice_client = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess]
        if voice_client:
            await voice_client.disconnect(force=True)

        embed = Embed(title="Leave mode has been activated", color=Color.green())
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
        await self._bot.store.set_guild_optional_search(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            optional_search=False,
        )

        embed = Embed(title="Auto search has been set", color=Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @_search_group.command(
        name="select",
        description="if a normal search is provided, the user will be able to select between multiple results",
    )
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def search_select(self, interaction: Interaction) -> None:
        await self._bot.store.set_guild_optional_search(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            optional_search=True,
        )

        embed = Embed(title="User-defined search has been set", color=Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

        await self._bot.store.set_guild_text_channel(
            guild_id,
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
