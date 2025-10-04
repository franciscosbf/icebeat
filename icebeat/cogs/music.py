import logging
import re
from typing import TYPE_CHECKING, Callable, Optional

from discord import (
    Client,
    Color,
    Embed,
    Interaction,
    InteractionResponseType,
    Member,
    Permissions,
    VoiceChannel,
    VoiceProtocol,
    app_commands,
)
from discord.abc import Connectable
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


_URL_RE = re.compile(r"^https?://(?:www\.)?.+")
_SEEK_TIME_RE = re.compile(
    r"^(((?P<hours>[1-9]\d*):(?P<mins_h>\d{2}))|(?P<mins_m>[1-9]{0,1}\d)):(?P<secs>\d{2})$"
)
_QUERY_SEARCH_FMT = "ytsearch:{}"
_MAX_SEARCHED_TRACKS = 6
_DEFAULT_PERMISSIONS = Permissions(
    connect=True,
    use_application_commands=True,
)
_PLAYER_BAR_SIZE = 20


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


def _bot_has_permissions() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    return app_commands.checks.bot_has_permissions(
        connect=True,
        speak=True,
        send_messages=True,
    )


class _LavalinkVoiceClient(VoiceProtocol):
    __slots__ = ("_lavalink_client", "_destroyed", "_guild")

    def __init__(self, client: Client, channel: Connectable) -> None:
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


class _MemberNotInVoiceChannel(app_commands.CheckFailure):
    pass


class _BotNotInVoiceChannel(app_commands.CheckFailure):
    pass


class _DifferentVoiceChannels(app_commands.CheckFailure):
    __slots__ = ("voice_channel_id",)

    def __init__(self, voice_channel_id: int) -> None:
        self.voice_channel_id = voice_channel_id


class _VoiceChannelIsFull(app_commands.CheckFailure):
    pass


def _ensure_player_is_ready() -> Callable[
    [app_commands.checks.T], app_commands.checks.T
]:
    async def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        player: lavalink.DefaultPlayer = bot.lavalink_client.player_manager.create(
            guild_id
        )

        bot_voice_client = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess]

        member: Member = interaction.user  # pyright: ignore[reportAssignmentType]
        if not member.voice or not member.voice.channel:
            raise _MemberNotInVoiceChannel()

        member_voice_channel = member.voice.channel
        if not bot_voice_client:
            if interaction.command.name != Music.play.name:  # pyright: ignore[reportOptionalMemberAccess]
                raise _BotNotInVoiceChannel()

            if member_voice_channel.user_limit > 0:
                if len(member_voice_channel.members) >= member_voice_channel.user_limit:
                    raise _VoiceChannelIsFull()

            guild_db = await bot.store.get_guild(guild_id)
            player.set_shuffle(guild_db.shuffle)
            await player.set_volume(guild_db.volume)

            await member_voice_channel.connect(cls=_LavalinkVoiceClient, self_deaf=True)

            return True

        bot_voice_channel: VoiceChannel = bot_voice_client.channel  # pyright: ignore[reportAssignmentType]
        if member_voice_channel.id != bot_voice_channel.id:
            raise _DifferentVoiceChannels(bot_voice_channel.id)

        return True

    return app_commands.check(predicate)


class _LavalinkFailedToGetTracks(app_commands.AppCommandError):
    __slots__ = ("load_result_error",)

    def __init__(self, load_result_error: lavalink.LoadResultError) -> None:
        self.load_result_error = load_result_error


def _milli_to_human_readable(duration: int) -> str:
    total_secs = int(duration / 1_000)
    total_mins = int(total_secs / 60)
    remaining_secs = total_secs % 60
    total_hours = int(total_mins / 60)
    remaining_mins = total_mins % 60

    formated = f":{'0' if remaining_secs < 10 else ''}{remaining_secs}"
    if total_hours > 0:
        formated = f"{total_hours}:{'0' if remaining_secs < 10 else ''}{remaining_mins}{formated}"
    else:
        formated = f"{remaining_mins}{formated}"

    return formated


class _SeekTimeTransformer(app_commands.Transformer):
    async def transform(
        self, interaction: Interaction, value: str
    ) -> Optional[tuple[int, str]]:
        _ = interaction

        match = _SEEK_TIME_RE.match(value)
        if not match:
            return None

        key_matches = match.groupdict()

        position = 0
        if hours := key_matches.get("hours"):
            position = (int(hours) * 3_600_000) + (int(key_matches["mins_h"]) * 60_000)
        else:
            position = int(key_matches["mins_m"]) * 60_000
        position += int(key_matches["secs"]) * 1_000

        return position, value


class _NotPlaying(app_commands.CheckFailure):
    pass


def _is_playing() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        player: lavalink.DefaultPlayer = bot.lavalink_client.player_manager.get(
            guild_id
        )  # pyright: ignore[reportAssignmentType]

        if not player.is_playing:
            raise _NotPlaying()

        return True

    return app_commands.check(predicate)


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

    async def _guild_still_exists(self, guild_id: int) -> bool:
        if self._bot.get_guild(guild_id):
            return True

        await self._lavalink_client.player_manager.destroy(guild_id)

        return False

    def _get_player(self, interaction: Interaction) -> Optional[lavalink.DefaultPlayer]:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        return self._bot.lavalink_client.player_manager.get(guild_id)  # pyright: ignore[reportReturnType]

    @lavalink.listener(lavalink.TrackStartEvent)
    async def on_track_start(self, event: lavalink.TrackStartEvent) -> None:
        await self._guild_still_exists(event.player.guild_id)

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

        if not await self._guild_still_exists(event.player.guild_id):
            return

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
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_cooldown()
    async def play(self, interaction: Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if not _URL_RE.match(query):
            query = _QUERY_SEARCH_FMT.format(query)

        result = await player.node.get_tracks(query)
        if result.load_type == lavalink.LoadType.EMPTY:
            embed = Embed(title="Couldn't find anything to play", color=Color.green())
            embed.set_footer(text="What kind of voodoo shi you trying to do on me?")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        elif result.load_type in (
            lavalink.LoadType.SEARCH,
            lavalink.LoadType.TRACK,
        ):
            tracks = result.tracks[:1]
        elif result.load_type == lavalink.LoadType.PLAYLIST:
            tracks = result.tracks
        else:
            raise _LavalinkFailedToGetTracks(result.error)  # pyright: ignore[reportArgumentType]

        for track in tracks:
            player.add(track, requester=interaction.user.id)

        ntracks = len(tracks)
        if ntracks == 1 and result.load_type != lavalink.LoadType.PLAYLIST:
            duration = _milli_to_human_readable(tracks[0].duration)
            embed = Embed(
                title="Track enqueued with success",
                description=f"**[{tracks[0].title}]({tracks[0].uri})** ┃ `{duration}`",
                color=Color.green(),
            )
        else:
            embed = Embed(
                title=f"Enqueued {ntracks} track{'s' if ntracks > 1 else ''} with success",
                description=f"**Playlist: [{result.playlist_info.name}]({query})**",
                color=Color.green(),
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

        if not player.is_playing:
            await player.play()

    @play.autocomplete("query")
    async def play_query_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if _URL_RE.match(current):
            return []

        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]
        player: lavalink.DefaultPlayer = (
            self._bot.lavalink_client.player_manager.create(guild_id)
        )

        query = _QUERY_SEARCH_FMT.format(current)
        result = await player.node.get_tracks(query)
        if result.load_type != lavalink.LoadType.SEARCH:
            return []

        tracks = result.tracks
        max_searches = min(len(tracks), _MAX_SEARCHED_TRACKS)
        return [
            app_commands.Choice(name=tracks[i].title, value=tracks[i].title)
            for i in range(max_searches)
        ]

    @app_commands.command(description="stops the player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_is_playing()
    @_cooldown()
    async def pause(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if not player.paused:
            await player.set_pause(True)

            embed = Embed(title="Player has been paused", color=Color.green())
        else:
            embed = Embed(title="Player is already paused", color=Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="resumes the player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_is_playing()
    @_cooldown()
    async def resume(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if player.paused:
            await player.set_pause(False)

            embed = Embed(title="Player has been resumed", color=Color.green())
        else:
            embed = Embed(title="Player is not paused", color=Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="skips what's currently playing")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_is_playing()
    @_cooldown()
    async def skip(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        await player.skip()

        embed = Embed(title="Skipped current track", color=Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="seeks to a given position")
    @app_commands.describe(position="track position like in the YouTube video player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_is_playing()
    @_cooldown()
    async def seek(
        self,
        interaction: Interaction,
        position: app_commands.Transform[
            Optional[tuple[int, str]], _SeekTimeTransformer
        ],
    ) -> None:
        if not position:
            embed = Embed(
                title="You must provide a valid position", color=Color.green()
            )
        else:
            player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

            position_milli, position_original = position

            if position_milli > player.current.duration:  # pyright: ignore[reportOptionalMemberAccess]
                current_track: lavalink.AudioTrack = player.current  # pyright: ignore[reportAssignmentType]
                track_duration = _milli_to_human_readable(current_track.duration)
                embed = Embed(
                    title="Track's shorter than the position you provided",
                    description=f"**Track duration:** `{track_duration}`\n\n**[{current_track.title}]({current_track.uri})**",
                    color=Color.green(),
                )
            else:
                await player.seek(position_milli)

                embed = Embed(
                    title=f"Seeked to position `{position_original}`",
                    color=Color.green(),
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="displays current track")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_is_playing()
    @_cooldown()
    async def current(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        voice_client: _LavalinkVoiceClient = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
        current_track: lavalink.AudioTrack = player.current  # pyright: ignore[reportAssignmentType]
        position = player.position
        current_time = _milli_to_human_readable(position)
        adjusted_bar = ["─"] * _PLAYER_BAR_SIZE
        adjusted_bar[int((position * _PLAYER_BAR_SIZE) / current_track.duration)] = (
            ":white_circle:"
        )
        max_time = _milli_to_human_readable(current_track.duration)
        player_bar = f"`{current_time}`┃{''.join(adjusted_bar)}┃`{max_time}`"
        embed = Embed(
            title=f"Playing at <#{voice_client.channel.id}>"
            f"{' (paused)' if player.paused else ''}",
            description=f"**[{current_track.title}]({current_track.uri})**\n\n{player_bar}",
            color=Color.green(),
        )
        if player.queue:
            queue_size = len(player.queue)
            if queue_size > 1:
                text = f"There're {queue_size} tracks in queue"
            else:
                text = f"There's {queue_size} track in queue"
            embed.set_footer(text=text)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="displays queued tracks")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_cooldown()
    async def queue(self, interaction: Interaction) -> None:
        _ = interaction
        # TODO: implement
        await interaction.response.send_message(
            content="Not implemented", ephemeral=True
        )

    @app_commands.command(description="forces me to disconnect from the voice channel")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_ensure_player_is_ready()
    @_cooldown()
    async def leave(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        player.queue.clear()
        await player.stop()

        voice_client: _LavalinkVoiceClient = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
        voice_channel = voice_client.channel
        await voice_client.disconnect(force=True)

        self._bot.lavalink_client.player_manager.remove(interaction.guild_id)  # pyright: ignore[reportArgumentType]

        embed = Embed(
            title=f"Bot has been disconnected from <#{voice_channel.id}>",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="enables/disables shuffle mode")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_cooldown()
    async def shuffle(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        shuffle = await self._bot.store.switch_guild_shuffle(guild_id)

        player = self._get_player(interaction)
        if player:
            player.set_shuffle(shuffle)

        embed = Embed(
            title=f"Shuffle mode has been {'enabled' if shuffle else 'disabled'}",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="change volume")
    @app_commands.describe(level="volume level (the higher, the worst)")
    @app_commands.guild_only()
    @_is_whitelisted()
    @_is_guild_owner()
    @_bot_has_permissions()
    @_cooldown()
    async def volume(
        self, interaction: Interaction, level: app_commands.Range[int, 0, 100]
    ) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        await self._bot.store.set_guild_volume(guild_id, volume=level)

        player = self._get_player(interaction)
        if player:
            await player.set_volume(vol=level)

        embed = Embed(title="Volume has been changed", color=Color.green())
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
        # TODO: implement
        await interaction.response.send_message(
            content="Not implemented", ephemeral=True
        )

    _presence_group = app_commands.Group(
        name="presence",
        description="decide bot behaviour when queue is empty",
        guild_only=True,
        default_permissions=_DEFAULT_PERMISSIONS,
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
        default_permissions=_DEFAULT_PERMISSIONS,
    )

    @app_commands.command(description="displays player info")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_whitelisted()
    @_bot_has_permissions()
    @_cooldown()
    async def player(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        embed = Embed(
            title="Player Info",
            color=Color.green(),
        )
        guild_db = await self._bot.store.get_guild(guild_id)  # pyright: ignore[reportArgumentType]
        shuffle_mode_state = "enabled" if guild_db.shuffle else "disabled"
        voice_client: Optional[_LavalinkVoiceClient] = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
        if voice_client:
            player = self._get_player(interaction)
            player_state = (
                f"in <#{voice_client.channel.id}>{' (paused)' if player.paused else ''}"  # pyright: ignore[reportOptionalMemberAccess]
            )
        else:
            player_state = "not connected"
        embed.add_field(name="Filter", value=guild_db.filter.name)
        embed.add_field(name="Volume", value=f"{guild_db.volume}")
        embed.add_field(name="Shuffle Mode", value=shuffle_mode_state, inline=False)
        embed.add_field(name="Player State", value=player_state)
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
        elif isinstance(error, _MemberNotInVoiceChannel):
            embed = Embed(
                title="You must be in a voice channel",
                color=Color.yellow(),
            )
            if bot_voice_client := interaction.guild.voice_client:  # pyright: ignore[reportOptionalMemberAccess]
                bot_voice_channel: VoiceChannel = bot_voice_client.channel  # pyright: ignore[reportAssignmentType]
                embed.description = (
                    f"Hop into <#{bot_voice_channel.id}>, I'm here to party."
                )
        elif isinstance(error, _BotNotInVoiceChannel):
            embed = Embed(
                title="I'm not in a voice channel",
                color=Color.yellow(),
            )
        elif isinstance(error, _DifferentVoiceChannels):
            embed = Embed(
                title="We aren't in the same voice channel",
                color=Color.yellow(),
            )
            embed.description = f"Come to <#{error.voice_channel_id}>"
        elif isinstance(error, _VoiceChannelIsFull):
            embed = Embed(
                title="Damn son, the channel's overflowing",
                color=Color.yellow(),
            )
            embed.set_footer(text="I meant it's full... duh")
        elif isinstance(error, _LavalinkFailedToGetTracks):
            load_result_error = error.load_result_error
            __log__.warning(
                "Failed to get tracks: message=%s, reason=%s",
                load_result_error.message,
                load_result_error.cause,
            )

            embed = Embed(
                title="Search didn't proceed as expected",
                description="I'm sorry, but my associate wasn't able to process your query",
                color=Color.yellow(),
            )
        elif isinstance(error, _NotPlaying):
            embed = Embed(title="There's no track in the player", color=Color.yellow())
        else:
            __log__.warning(
                f"Error on {interaction.command.name} command",  # pyright: ignore[reportOptionalMemberAccess]
                exc_info=True,
            )

            embed = Embed(
                title="Something unexpected went wrong...",
                color=Color.red(),
            )

        if (
            interaction.response.type
            == InteractionResponseType.deferred_channel_message
        ):
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
