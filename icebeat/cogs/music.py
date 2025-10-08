import logging
import re
from typing import TYPE_CHECKING, Callable, Optional

from discord import (
    Client,
    Color,
    Embed,
    Interaction,
    Member,
    Permissions,
    VoiceChannel,
    VoiceProtocol,
    VoiceState,
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
_MAX_QUEUE_SIZE = 400
_ORDINAL_SUFFIX = (
    "th",
    "st",
    "nd",
    "rd",
    "th",
    "th",
    "th",
    "th",
    "th",
    "th",
    "th",
    "th",
    "th",
    "th",
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


class _FailedToRetrievePlayer(app_commands.CheckFailure):
    __slots__ = ("original_error",)

    def __init__(self, original_error: Exception) -> None:
        self.original_error = original_error


class _FailedToPreparePlayer(app_commands.CheckFailure):
    __slots__ = ("original_error",)

    def __init__(self, original_error: Exception) -> None:
        self.original_error = original_error


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


def _loop_mode(loop: bool) -> int:
    return (
        lavalink.DefaultPlayer.LOOP_QUEUE if loop else lavalink.DefaultPlayer.LOOP_NONE
    )


def _ensure_player_is_ready() -> Callable[
    [app_commands.checks.T], app_commands.checks.T
]:
    async def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        try:
            player: lavalink.DefaultPlayer = bot.lavalink_client.player_manager.create(
                guild_id
            )
        except Exception as e:
            raise _FailedToRetrievePlayer(e)

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

            try:
                guild_db = await bot.store.get_guild(guild_id)
                await player.set_volume(guild_db.volume)
            except Exception as e:
                raise _FailedToPreparePlayer(e)
            player.set_shuffle(guild_db.shuffle)
            player.set_loop(_loop_mode(guild_db.loop))

            await member_voice_channel.connect(cls=_LavalinkVoiceClient, self_deaf=True)

            return True

        bot_voice_channel: VoiceChannel = bot_voice_client.channel  # pyright: ignore[reportAssignmentType]
        if member_voice_channel.id != bot_voice_channel.id:
            raise _DifferentVoiceChannels(bot_voice_channel.id)

        return True

    return app_commands.check(predicate)


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

        player: lavalink.DefaultPlayer = bot.lavalink_client.player_manager.create(
            guild_id
        )  # pyright: ignore[reportAssignmentType]

        if not player.is_playing:
            raise _NotPlaying()

        return True

    return app_commands.check(predicate)


def _to_ordinal(value: int) -> str:
    r = value % 100
    suffix = _ORDINAL_SUFFIX[r] if r <= 13 else _ORDINAL_SUFFIX[value % 10]

    return f"{value}{suffix}"


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

    async def _disconnect_bot(
        self, player: lavalink.DefaultPlayer, voice_client: _LavalinkVoiceClient
    ) -> None:
        player.queue.clear()
        await player.stop()

        await voice_client.disconnect(force=True)

    @lavalink.listener(lavalink.TrackStartEvent)
    async def on_track_start(self, event: lavalink.TrackStartEvent) -> None:
        await self._guild_still_exists(event.player.guild_id)

    @lavalink.listener(lavalink.TrackStuckEvent)
    async def on_track_stuck(self, event: lavalink.TrackStuckEvent) -> None:
        __log__.warning(
            "Track %s got stuck in guild %d player",
            event.track.source_name,
            event.player.guild_id,
        )

    @lavalink.listener(lavalink.TrackExceptionEvent)
    async def on_track_exception(self, event: lavalink.TrackExceptionEvent) -> None:
        __log__.warning(
            "Track %s raised playback error on guild %d: %s",
            event.track.source_name,
            event.player.guild_id,
            event.cause,
        )

    @lavalink.listener(lavalink.TrackLoadFailedEvent)
    async def on_track_load_failed(self, event: lavalink.TrackLoadFailedEvent) -> None:
        __log__.warning(
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
        except Exception as e:
            __log__.warning(
                "Encountered an error when trying to play next enqueued track: %s",
                e,
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
        __log__.warning(
            "Player raised an error: %s",
            event.original,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: Member, before: VoiceState, after: VoiceState
    ) -> None:
        player: Optional[lavalink.DefaultPlayer] = (
            self._bot.lavalink_client.player_manager.get(member.guild.id)
        )
        if not player:
            return

        voice_client: Optional[_LavalinkVoiceClient] = member.guild.voice_client  # pyright: ignore[reportAssignmentType]
        if not voice_client:
            return

        channel_id = voice_client.channel.id
        if (before.channel and before.channel.id == channel_id) or (
            after.channel and after.channel.id == channel_id
        ):
            voice_states = voice_client.channel.voice_states
            if len(voice_states) == 1 and self._bot.user.id in voice_states:  # pyright: ignore[reportOptionalMemberAccess]
                await self._disconnect_bot(player, voice_client)

    @app_commands.command(description="Request something to play")
    @app_commands.describe(query="link or normal search as if you were on YouTube")
    @app_commands.guild_only()
    @_default_permissions()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def play(self, interaction: Interaction, query: str) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        free_queue_slots = _MAX_QUEUE_SIZE - len(player.queue)
        if free_queue_slots == 0:
            embed = Embed(title="Queue is full", color=Color.green())
            embed.set_footer(text=f"Queue only supports up to {_MAX_QUEUE_SIZE} tracks")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            result = await player.node.get_tracks(
                query if _URL_RE.match(query) else _QUERY_SEARCH_FMT.format(query)
            )
        except Exception as e:
            __log__.warning("Failed to request tracks: %v", e)

            embed = Embed(
                title="Search didn't proceed as expected",
                color=Color.green(),
            )
            embed.set_footer(text="I wasn't able to contact my assistant")
            await interaction.followup.send(embed=embed)

            return

        if result.load_type == lavalink.LoadType.EMPTY:
            embed = Embed(title="Couldn't find anything to play", color=Color.green())
            embed.set_footer(text="What kind of voodoo shi you trying to do on me?")
            await interaction.followup.send(embed=embed)
            return
        elif result.load_type == lavalink.LoadType.SEARCH:
            for i in range(min(len(result.tracks), _MAX_SEARCHED_TRACKS)):
                if result.tracks[i].title == query:
                    tracks = [result.tracks[i]]
                    break
            else:
                tracks = result.tracks[:1]
        elif result.load_type == lavalink.LoadType.TRACK:
            tracks = result.tracks[:1]
        elif result.load_type == lavalink.LoadType.PLAYLIST:
            tracks = result.tracks
        else:
            error: lavalink.LoadResultError = result.error  # pyright: ignore[reportAssignmentType]

            __log__.warning(
                "Failed to get tracks: message=%s, reason=%s",
                error.message,
                error.cause,
            )

            embed = Embed(
                title="Sadly, I received an error from my partner", color=Color.green()
            )
            embed.set_footer(
                text="My associate had a problem while processing your search"
            )
            await interaction.followup.send(embed=embed)

            return

        n_retrieved_tracks = len(tracks)
        n_enqueued_tracks = min(free_queue_slots, n_retrieved_tracks)
        for i in range(n_enqueued_tracks):
            player.add(tracks[i], requester=interaction.user.id)

        if len(tracks) == 1 and result.load_type != lavalink.LoadType.PLAYLIST:
            duration = _milli_to_human_readable(tracks[0].duration)
            embed = Embed(
                title="Track enqueued with success",
                description=f"**[{tracks[0].title}]({tracks[0].uri})** ┃ `{duration}`",
                color=Color.green(),
            )
        else:
            embed = Embed(
                title=f"Enqueued {n_enqueued_tracks} track{'s' if free_queue_slots > 1 else ''} with success",
                description=f"**Playlist: [{result.playlist_info.name}]({query})**",
                color=Color.green(),
            )
            if n_enqueued_tracks < n_retrieved_tracks:
                embed.set_footer(
                    text=f"Were retrieved {n_retrieved_tracks} track"
                    f"{'s' if n_retrieved_tracks > 1 else ''} from the playlist, although\n"
                    f"the queue has reached its full capacity ({_MAX_QUEUE_SIZE} tracks)"
                )
        await interaction.followup.send(embed=embed)

        if not player.is_playing:
            await player.play()

    @play.autocomplete("query")
    @_is_whitelisted()
    async def query_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if _URL_RE.match(current):
            return []

        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]
        try:
            player: lavalink.DefaultPlayer = (
                self._bot.lavalink_client.player_manager.create(guild_id)
            )
        except Exception as e:
            __log__.warning(
                "Failed to retrieve guild player while processing "
                "autocomplete call for play command: %s",
                e,
            )

            return []

        query = _QUERY_SEARCH_FMT.format(current)
        try:
            result = await player.node.get_tracks(query)
        except Exception as e:
            __log__.warning(
                "Failed to request tracks while autocompleting"
                "query argument of play command: %s",
                e,
            )

            return []

        if result.load_type != lavalink.LoadType.SEARCH:
            return []

        tracks = result.tracks
        max_searches = min(len(tracks), _MAX_SEARCHED_TRACKS)
        return [
            app_commands.Choice(name=tracks[i].title, value=tracks[i].title)
            for i in range(max_searches)
        ]

    @app_commands.command(description="Stops the player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_playing()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def pause(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if not player.paused:
            await player.set_pause(True)

            embed = Embed(title="Player has been paused", color=Color.green())
            ephemeral = False
        else:
            embed = Embed(title="Player is already paused", color=Color.green())
            ephemeral = True
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(description="Resumes the player")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_playing()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def resume(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if player.paused:
            await player.set_pause(False)

            embed = Embed(title="Player has been resumed", color=Color.green())
            ephemeral = False
        else:
            embed = Embed(title="Player is not paused", color=Color.green())
            ephemeral = True
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(description="Skips current track")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_playing()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def skip(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        current_track: lavalink.AudioTrack = player.current  # pyright: ignore[reportAssignmentType]

        await player.skip()

        embed = Embed(
            title="Skipped current track",
            description=f"**I was playing [{current_track.title}]({current_track.uri})**",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Jumps to a given enqueued track")
    @app_commands.describe(position="track position in queue")
    @app_commands.guild_only()
    @_default_permissions()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def jump(
        self,
        interaction: Interaction,
        position: app_commands.Range[int, 1, None],
    ) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        ephemeral = True
        if not player.queue:
            embed = Embed(
                title="Queue is empty",
                color=Color.green(),
            )
        else:
            queue_size = len(player.queue)
            ordinal_position = _to_ordinal(position)
            if position <= queue_size:
                if position > 1:
                    player.queue = player.queue[position - 1 :]
                next_track = player.queue[0]

                await player.skip()

                embed = Embed(
                    title=f"Jumping to the {ordinal_position} track in queue",
                    description=f"**[{next_track.title}]({next_track.uri})**",
                    color=Color.green(),
                )
                ephemeral = False
            else:
                embed = Embed(
                    title=f"Queue has {queue_size} track{'s' if queue_size > 1 else ''} "
                    f"and you tried to jump to the {ordinal_position} track",
                    color=Color.green(),
                )
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(description="Removes a track from queue given its position")
    @app_commands.describe(position="track position in queue")
    @app_commands.guild_only()
    @_default_permissions()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def pop(
        self,
        interaction: Interaction,
        position: app_commands.Range[int, 1, None],
    ) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        ephemeral = True
        if not player.queue:
            embed = Embed(
                title="Queue is empty",
                color=Color.green(),
            )
        else:
            queue_size = len(player.queue)
            ordinal_position = _to_ordinal(position)
            if position <= queue_size:
                removed_track = player.queue[position - 1]
                player.queue.pop(position - 1)

                embed = Embed(
                    title=f"Successfully popped the {ordinal_position} track from queue",
                    description=f"**[{removed_track.title}]({removed_track.uri})**",
                    color=Color.green(),
                )
                ephemeral = False
            else:
                embed = Embed(
                    title=f"Queue has {queue_size} track{'s' if queue_size > 1 else ''} "
                    f"and you tried to remove the {ordinal_position} track",
                    color=Color.green(),
                )
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @jump.autocomplete("position")
    @pop.autocomplete("position")
    @_bot_has_permissions()
    @_is_whitelisted()
    async def position_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not current.isdigit():
            return []

        player: Optional[lavalink.DefaultPlayer] = self._get_player(interaction)
        if not player:
            return []

        position = int(current)
        if not 1 <= position <= len(player.queue):
            return []

        track = player.queue[position - 1]

        return [app_commands.Choice(name=track.title, value=position)]

    @app_commands.command(description="Seeks to a given position in the track")
    @app_commands.describe(
        position="track position like in the YouTube video player, like 5:38"
    )
    @app_commands.guild_only()
    @_default_permissions()
    @_is_playing()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def seek(
        self,
        interaction: Interaction,
        position: app_commands.Transform[
            Optional[tuple[int, str]], _SeekTimeTransformer
        ],
    ) -> None:
        ephemeral = True
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
                ephemeral = False
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(description="Displays current track")
    @app_commands.guild_only()
    @_default_permissions()
    @_is_playing()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
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
        player_bar = f"`{current_time}` ┃{''.join(adjusted_bar)}┃ `{max_time}`"
        embed = Embed(
            title=f"Playing at <#{voice_client.channel.id}>"
            f"{' (paused)' if player.paused else ''}",
            description=f"**[{current_track.title}]({current_track.uri})**\n\n"
            f"{player_bar}\n\n**Enqueued by** <@{current_track.requester}>",
            color=Color.green(),
        )
        if player.queue:
            queue_size = len(player.queue)
            if queue_size > 1:
                text = f"There're {queue_size} tracks in queue"
            else:
                text = "There's 1 track in queue"
            embed.set_footer(text=text)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Lists queue")
    @app_commands.guild_only()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def queue(self, interaction: Interaction) -> None:
        _ = interaction
        # TODO: implement
        await interaction.response.send_message(
            content="Not implemented", ephemeral=True
        )

    @app_commands.command(description="Removes all queued tracks")
    @app_commands.guild_only()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def wipe(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if player.queue:
            player.queue = []

            embed = Embed(
                title="The queue is now empty",
                color=Color.green(),
            )
            ephemeral = False
        else:
            embed = Embed(
                title="There are no queued tracks",
                color=Color.green(),
            )
            ephemeral = True
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(description="Forces me to disconnect from the voice channel")
    @app_commands.guild_only()
    @_default_permissions()
    @_ensure_player_is_ready()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def leave(self, interaction: Interaction) -> None:
        player: lavalink.DefaultPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        voice_client: _LavalinkVoiceClient = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
        await self._disconnect_bot(player, voice_client)

        embed = Embed(
            title=f"Bot has been disconnected from <#{voice_client.channel.id}>",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Toggles queue's shuffle mode")
    @app_commands.guild_only()
    @_default_permissions()
    @_bot_has_permissions()
    @_is_guild_owner()
    @_cooldown()
    @_is_whitelisted()
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
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Toggles queue's loop mode")
    @app_commands.guild_only()
    @_default_permissions()
    @_bot_has_permissions()
    @_is_guild_owner()
    @_cooldown()
    @_is_whitelisted()
    async def loop(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        loop = await self._bot.store.switch_guild_shuffle(guild_id)

        player = self._get_player(interaction)
        if player:
            player.set_loop(_loop_mode(loop))

        embed = Embed(
            title=f"Loop mode has been {'enabled' if loop else 'disabled'}",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Changes player volume")
    @app_commands.describe(level="volume level (the higher, the worst)")
    @app_commands.guild_only()
    @_bot_has_permissions()
    @_is_guild_owner()
    @_cooldown()
    @_is_whitelisted()
    async def volume(
        self, interaction: Interaction, level: app_commands.Range[int, 0, 100]
    ) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        await self._bot.store.set_guild_volume(guild_id, volume=level)

        player = self._get_player(interaction)
        if player:
            await player.set_volume(vol=level)

        embed = Embed(title="Volume has been changed", color=Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Sets player filter")
    @app_commands.describe(name="filter name")
    @app_commands.guild_only()
    @_bot_has_permissions()
    @_is_guild_owner()
    @_cooldown()
    @_is_whitelisted()
    async def filter(self, interaction: Interaction, name: Filter) -> None:
        _, _ = interaction, name
        # TODO: implement
        await interaction.response.send_message(content="Not implemented")

    _presence_group = app_commands.Group(
        name="presence",
        description="Decides bot behaviour when queue is empty (the bot leaves the voice channel when it's alone)",
        guild_only=True,
        default_permissions=_DEFAULT_PERMISSIONS,
    )

    @_presence_group.command(
        name="stay",
        description="Bot won’t leave the voice channel when the queue's empty",
    )
    @_bot_has_permissions()
    @_is_guild_owner()
    @_cooldown()
    @_is_whitelisted()
    async def presence_stay(self, interaction: Interaction) -> None:
        await self._bot.store.set_guild_auto_leave(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            auto_leave=False,
        )

        embed = Embed(title="Stay mode has been activated", color=Color.green())
        await interaction.response.send_message(embed=embed)

    @_presence_group.command(
        name="leave",
        description="Bot will leave the voice channel when the queue's empty",
    )
    @_bot_has_permissions()
    @_is_guild_owner()
    @_cooldown()
    @_is_whitelisted()
    async def presence_leave(self, interaction: Interaction) -> None:
        await self._bot.store.set_guild_auto_leave(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            auto_leave=True,
        )

        voice_client = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess]
        if voice_client:
            await voice_client.disconnect(force=True)

        embed = Embed(title="Leave mode has been activated", color=Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Displays player info")
    @app_commands.guild_only()
    @_default_permissions()
    @_bot_has_permissions()
    @_cooldown()
    @_is_whitelisted()
    async def player(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        embed = Embed(
            title="Player Info",
            color=Color.green(),
        )
        guild_db = await self._bot.store.get_guild(guild_id)  # pyright: ignore[reportArgumentType]
        shuffle_mode_state = "enabled" if guild_db.shuffle else "disabled"
        loop_mode_state = "enabled" if guild_db.loop else "disabled"
        bot_presence = "stay" if guild_db.auto_leave else "leave"
        voice_client: Optional[_LavalinkVoiceClient] = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
        if voice_client:
            player = self._get_player(interaction)
            player_state = (
                f"in <#{voice_client.channel.id}>{' (paused)' if player.paused else ''}"  # pyright: ignore[reportOptionalMemberAccess]
            )
        else:
            player_state = "not connected"
        embed.add_field(
            name="┃ Filter :level_slider:",
            value=f"- {guild_db.filter.name}",
        )
        embed.add_field(name="┃ Volume :sound:", value=f"- {guild_db.volume}")
        embed.add_field(
            name="┃ Shuffle Mode :twisted_rightwards_arrows:",
            value=f"- {shuffle_mode_state}",
        )
        embed.add_field(
            name="┃ Loop Mode :arrows_counterclockwise:",
            value=f"- {loop_mode_state}",
        )
        embed.add_field(
            name="┃ Presence on Empty Queue :hand_splayed:",
            value=f"- {bot_presence}",
        )
        embed.add_field(name="┃ State :notes:", value=f"- {player_state}")
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
        elif isinstance(error, _FailedToRetrievePlayer):
            __log__.warning("Failed to retrieve guild player: %v", error.original_error)

            embed = Embed(
                title="My assistant just disapeared...",
                color=Color.yellow(),
            )
            embed.set_footer(text="He just doesn't respond to my orders!?")
        elif isinstance(error, _FailedToPreparePlayer):
            __log__.warning("Failed to prepare guild player: %v", error.original_error)

            embed = Embed(
                title="A problem occurred when preparing the player",
                color=Color.yellow(),
            )
            embed.set_footer(text="Everything is fine, it wasn't your fault")
        elif isinstance(error, _MemberNotInVoiceChannel):
            embed = Embed(
                title="You must be in a voice channel",
                color=Color.yellow(),
            )
            if bot_voice_client := interaction.guild.voice_client:  # pyright: ignore[reportOptionalMemberAccess]
                bot_voice_channel: VoiceChannel = bot_voice_client.channel  # pyright: ignore[reportAssignmentType]
                embed.description = (
                    f"Hop into <#{bot_voice_channel.id}>, I'm here to party"
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
        elif isinstance(error, _NotPlaying):
            embed = Embed(title="There's no track in the player", color=Color.yellow())
        else:
            __log__.warning(
                f"Error on {interaction.command.name} command",  # pyright: ignore[reportOptionalMemberAccess]
                exc_info=True,
            )

            embed = Embed(
                title="Something bad has happened and I dunno why...",
                color=Color.red(),
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)
