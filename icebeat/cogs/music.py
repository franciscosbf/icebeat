import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Callable, Optional, Union
from attr import dataclass
from typing_extensions import override

from discord import (
    Client,
    ClientException,
    Color,
    Embed,
    Guild,
    HTTPException,
    Interaction,
    Member,
    NotFound,
    Permissions,
    Role,
    VoiceChannel,
    VoiceProtocol,
    VoiceState,
    Webhook,
    app_commands,
)
from discord.abc import Connectable, Snowflake
from discord.types.voice import (
    GuildVoiceState as GuildVoiceStatePayload,
    VoiceServerUpdate as VoiceServerUpdatePayload,
)
from discord.ext import commands
import lavalink

from icebeat.notify import Waiter
from icebeat.ui import InteractionPagination, Page, compute_total_pages


from ..model import Filter
from ..player import IceBeatPlayer, Queue
from ..treesync import (
    AppCommands,
    RegisteredAppCommands,
    RemovedAppCommands,
    tree_sync_listener,
)

if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Music"]

__log__ = logging.getLogger(__name__)


_MAX_DISCORD_TEXT_LINK_SIZE = 55
_URL_RE = re.compile(r"^https?://(?:www\.)?.+")
_SEEK_TIME_RE = re.compile(
    r"^(((?P<hours>[1-9]\d*):(?P<mins_h>\d{2}))|(?P<mins_m>[1-9]{0,1}\d)):(?P<secs>\d{2})$"
)
_QUERY_SEARCH_FMT = "ytsearch:{}"
_MAX_SEARCH_RESULTS = 8
_DEFAULT_USER_PERMISSIONS = Permissions(
    connect=True,
    use_application_commands=True,
)
_PLAYER_BAR_SIZE = 20
_QUEUE_PAGINATION_TIMEOUT = 40.0
_QUEUE_PAGE_SIZE = 6
_CURRENT_TRACK_MSG_TIMEOUT = 16.0
_CURRENT_TRACK_MSG_EDIT_TIMEOUT = 1.0
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
_FILTER_PRESETS = {
    Filter.bassboost: lavalink.Equalizer(
        gains=[
            0.18,
            0.2,
            0.18,
            0.1,
            0.05,
            0.0,
            0.0,
            0.0,
            0.02,
            0.03,
            0.05,
            0.06,
            0.05,
            0.03,
            0.0,
        ]
    ),
    Filter.pop: lavalink.Equalizer(
        [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.15,
            0.15,
            0.15,
            0.15,
            0.15,
            0.15,
            0.15,
        ]
    ),
    Filter.soft: lavalink.LowPass(),
    Filter.treblebass: lavalink.Equalizer(
        gains=[
            -0.3,
            -0.3,
            -0.2,
            -0.1,
            0.0,
            0.1,
            0.2,
            0.3,
            0.4,
            0.5,
            0.6,
            0.7,
            0.7,
            0.7,
            0.7,
        ]
    ),
    Filter.eightd: lavalink.Rotation(rotation_hz=0.2),
    Filter.karaoke: lavalink.Karaoke(),
    Filter.vaporwave: (
        lavalink.Equalizer(gains=[0.3, 0.3]),
        lavalink.Timescale(speed=0.9, pitch=0.8),
        lavalink.Tremolo(depth=0.3),
    ),
    Filter.nightcore: lavalink.Timescale(speed=1.2, pitch=1.2),
}


def _format_hyperlink(text: str, link: str) -> str:
    if len(text) > _MAX_DISCORD_TEXT_LINK_SIZE:
        text = f"{text[:_MAX_DISCORD_TEXT_LINK_SIZE]}…"

    text = text.replace("[", "⌈")
    text = text.replace("]", "⌉")
    text = text.replace("*", "∗")

    return f"[{text}]({link})"


def _default_user_permissions() -> Callable[
    [app_commands.checks.T], app_commands.checks.T
]:
    return app_commands.default_permissions(_DEFAULT_USER_PERMISSIONS)


def _cooldown() -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    def factory(interaction: Interaction) -> app_commands.Cooldown:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]

        return app_commands.Cooldown(
            rate=bot.cooldown_preset.rate, per=bot.cooldown_preset.time
        )

    return app_commands.checks.dynamic_cooldown(
        factory=factory,
        key=lambda interaction: interaction.guild_id,
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


class _NotGuildOwnerNorStaff(app_commands.CheckFailure):
    __slots__ = ("staff_role_id",)

    def __init__(self, staff_role_id: Optional[int]) -> None:
        self.staff_role_id = staff_role_id


def _staff_only():
    def decorator(command_callback):
        setattr(command_callback, "__staff__", None)

        return command_callback

    return decorator


def _is_staff_command(command: app_commands.Command) -> bool:
    return hasattr(command.callback, "__staff__")


def _is_guild_owner_or_staff() -> Callable[
    [app_commands.checks.T], app_commands.checks.T
]:
    async def predicate(interaction: Interaction) -> bool:
        member: Member = interaction.user  # pyright: ignore[reportAssignmentType]
        guild: Guild = interaction.guild  # pyright: ignore[reportAssignmentType]

        if member.id == guild.owner_id:
            return True

        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]

        guild_db = await bot.store.get_guild(guild.id)
        if guild_db.staff_role_id:
            if not guild.get_role(guild_db.staff_role_id):
                await bot.store.unset_guild_staff_role_id_if_same(
                    guild.id, guild_db.staff_role_id
                )
            elif member.get_role(guild_db.staff_role_id):
                return True

        raise _NotGuildOwnerNorStaff(guild_db.staff_role_id)

    return app_commands.check(predicate)


def _bot_has_permissions(
    **perms: bool,
) -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    return app_commands.checks.bot_has_permissions(**perms)


def _prettify_missing_bot_permissions(error: app_commands.BotMissingPermissions) -> str:
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

    return fmted_perms


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

    @override
    async def on_voice_state_update(self, data: GuildVoiceStatePayload) -> None:
        raw_channel_id = data["channel_id"]
        if not raw_channel_id:
            await self._destroy()

            return

        channel_id = int(raw_channel_id)
        self.channel: VoiceChannel = self.client.get_channel(channel_id)  # pyright: ignore[reportAttributeAccessIssue, reportIncompatibleVariableOverride]

        payload = {"t": "VOICE_STATE_UPDATE", "d": data}
        await self._lavalink_client.voice_update_handler(payload)  # pyright: ignore[reportArgumentType]

    @override
    async def on_voice_server_update(self, data: VoiceServerUpdatePayload) -> None:
        payload = {"t": "VOICE_SERVER_UPDATE", "d": data}
        await self._lavalink_client.voice_update_handler(payload)  # pyright: ignore[reportArgumentType]

    @override
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

    @override
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


class _BotMissingPermissionsInVoiceChannel(app_commands.BotMissingPermissions):
    __slots__ = ("voice_channel_id",)

    def __init__(self, missing_permissions: list[str], voice_channel_id: int) -> None:
        super().__init__(missing_permissions)

        self.voice_channel_id = voice_channel_id


class _BotRoleMissingPermissionsInVoiceChannel(_BotMissingPermissionsInVoiceChannel):
    __slots__ = ("role_id",)

    def __init__(
        self, missing_permissions: list[str], voice_channel_id: int, role_id: int
    ) -> None:
        super().__init__(missing_permissions, voice_channel_id)

        self.role_id = role_id


def _parse_loop_mode(loop: bool) -> int:
    return IceBeatPlayer.LOOP_QUEUE if loop else IceBeatPlayer.LOOP_NONE


async def _set_filter_preset(player: IceBeatPlayer, filter: Filter) -> None:
    if filter == Filter.normal:
        await player.clear_filters()
        return

    filter_preset = _FILTER_PRESETS[filter]
    if filter == Filter.vaporwave:
        await player.set_filters(*filter_preset)
    else:
        await player.set_filter(filter_preset)


def _collect_missing_perms(existing_perms, **required_perms: bool):
    return [
        perm
        for perm, value in required_perms.items()
        if getattr(existing_perms, perm) != value
    ]


def _check_vc_perms_for_bot(channel: VoiceChannel, **required_perms: bool) -> None:
    me: Member = channel.guild.me

    channel_perms_for_me = channel.permissions_for(me)
    missing_bot_perms = _collect_missing_perms(channel_perms_for_me, **required_perms)
    if missing_bot_perms:
        raise _BotMissingPermissionsInVoiceChannel(missing_bot_perms, channel.id)

    for role in me.roles:
        channel_perms_for_role = channel.permissions_for(role)
        missing_bot_role_perms = _collect_missing_perms(channel_perms_for_role)
        if missing_bot_role_perms:
            raise _BotRoleMissingPermissionsInVoiceChannel(
                missing_bot_role_perms, channel.id, role.id
            )


def _check_vc_user_limit(channel: VoiceChannel) -> None:
    # channel.user_limit == 0 -> channel user limit is infinite
    if channel.user_limit > 0:
        if len(channel.members) >= channel.user_limit:
            raise _VoiceChannelIsFull()


async def _prepare_player(bot: "IceBeat", player: IceBeatPlayer, guild_id: int) -> None:
    try:
        guild_db = await bot.store.get_guild(guild_id)
        await player.set_volume(guild_db.volume)
        await _set_filter_preset(player, guild_db.filter)
    except Exception as e:
        raise _FailedToPreparePlayer(e)
    player.set_shuffle(guild_db.shuffle)
    player.set_loop(_parse_loop_mode(guild_db.loop))


def _ensure_player_is_ready(
    bypass_channel_presence_check: bool = False,
) -> Callable[[app_commands.checks.T], app_commands.checks.T]:
    async def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        try:
            player: IceBeatPlayer = bot.lavalink_client.player_manager.create(guild_id)
        except Exception as e:
            raise _FailedToRetrievePlayer(e)

        if bypass_channel_presence_check:
            return True

        bot_voice_client = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess]

        member: Member = interaction.user  # pyright: ignore[reportAssignmentType]
        if not member.voice or not member.voice.channel:
            raise _MemberNotInVoiceChannel()

        member_voice_channel: VoiceChannel = member.voice.channel  # pyright: ignore[reportAssignmentType]
        if not bot_voice_client:
            if interaction.command.name != Music.play.name:  # pyright: ignore[reportOptionalMemberAccess]
                raise _BotNotInVoiceChannel()

            _check_vc_perms_for_bot(member_voice_channel, connect=True, speak=True)
            _check_vc_user_limit(member_voice_channel)

            await _prepare_player(bot, player, guild_id)

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

        player: IceBeatPlayer = bot.lavalink_client.player_manager.create(guild_id)  # pyright: ignore[reportAssignmentType]

        if not player.is_playing:
            raise _NotPlaying()

        return True

    return app_commands.check(predicate)


def _to_ordinal(value: int) -> str:
    r = value % 100
    suffix = _ORDINAL_SUFFIX[r] if r <= 13 else _ORDINAL_SUFFIX[value % 10]

    return f"{value}{suffix}"


@dataclass
class _CommandInfo:
    id: int
    qualified_name: str
    description: str


class _QueuePage(Page):
    __slots__ = (
        "_player_manager",
        "_player",
        "_current_waiter",
        "_current_waiter_task",
        "_queue_waiter",
        "_queue_waiter_task",
    )

    def __init__(
        self,
        bot: IceBeat,
        guild: Snowflake,
    ) -> None:
        self._player_manager = bot.lavalink_client.player_manager
        self._player: IceBeatPlayer = self._player_manager.get(guild.id)  # pyright: ignore[reportAttributeAccessIssue]
        self._current_waiter = self._player.current_waiter()
        self._current_waiter_task: Optional[asyncio.Task[Any]] = None
        self._queue_waiter = self._player.queue.waiter()
        self._queue_waiter_task: Optional[asyncio.Task[Any]] = None

    def _unavailable_page_alert(self) -> Embed:
        return Embed(
            title="Queue list is no longer available",
            description=f"Type **/{Music.queue.qualified_name}** to list queued tracks",
            color=Color.green(),
        )

    def _valid_player(self) -> bool:
        return self._player is self._player_manager.get(self._player.guild_id)

    async def fetch(self, current_page: int) -> tuple[Embed, int, int, bool]:
        if not self._valid_player():
            return self._unavailable_page_alert(), 1, 1, True

        if not (queue := self._player.queue):
            embed = Embed(
                title="Queue is empty",
                color=Color.green(),
            )
            return embed, 1, 1, True

        embed = Embed(title="Queue", color=Color.green())

        if current := self._player.current:
            embed.add_field(
                name="Current",
                value=f"**{_format_hyperlink(current.title, current.uri)}**",
            )

        queue_len = len(queue)
        total_pages = compute_total_pages(queue_len, _QUEUE_PAGE_SIZE)
        if current_page > total_pages:
            current_page = total_pages
        queue_page_start = (current_page - 1) * _QUEUE_PAGE_SIZE
        queue_page_end = min(queue_page_start + _QUEUE_PAGE_SIZE, len(queue))
        queue_page = enumerate(
            (queue[i] for i in range(queue_page_start, queue_page_end)),
            start=queue_page_start + 1,
        )
        embed.add_field(
            name="Upcoming",
            value="\n".join(
                "**{}.** **{}**".format(
                    pos,
                    _format_hyperlink(track.title, track.uri),
                )
                for pos, track in queue_page
            ),
            inline=False,
        )

        embed.set_footer(text=f"page {current_page}/{total_pages}")

        return embed, current_page, total_pages, False

    def unavailable_page_alert(self) -> Embed:
        return self._unavailable_page_alert()

    async def wait_for_edit_request(self) -> None:
        if not self._current_waiter_task:
            self._current_waiter_task = asyncio.create_task(self._current_waiter.wait())
        if not self._queue_waiter_task:
            self._queue_waiter_task = asyncio.create_task(self._queue_waiter.wait())

        done, _ = await asyncio.wait(
            (self._current_waiter_task, self._queue_waiter_task),
            return_when=asyncio.FIRST_COMPLETED,
        )

        if self._current_waiter_task in done:
            self._current_waiter_task = None
        if self._queue_waiter_task in done:
            self._queue_waiter_task = None

    def cancel_edit_request(self) -> None:
        self._queue_waiter.done()


class Music(commands.Cog):
    __slots__ = (
        "_bot",
        "_lavalink_client",
        "_staff_commands",
        "_cached_guild_staff_commands_info",
    )

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot
        self._lavalink_client = self._setup_lavalink()
        self._staff_commands: list[
            tuple[app_commands.Command, Optional[app_commands.Group]]
        ] = self._group_staff_commands()
        self._cached_guild_staff_commands_info: dict[int, list[_CommandInfo]] = {}

        self._bot.lavalink_client = self._lavalink_client

    def _setup_lavalink(self) -> lavalink.Client:
        if queue_size := self._bot.conf.player.queue_size:
            Queue.set_max_size(queue_size)

        lavalink_client = lavalink.Client(self._bot.user.id, player=IceBeatPlayer)  # pyright: ignore reportOptionalMemberAccess

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

    def _group_staff_commands(
        self,
    ) -> list[tuple[app_commands.Command, Optional[app_commands.Group]]]:
        staff_commands = []

        def walk_group_commands(
            base_group: app_commands.Group,
            command: Union[app_commands.Command, app_commands.Group],
            staff_commands: list[
                tuple[app_commands.Command, Optional[app_commands.Group]]
            ],
        ) -> None:
            if isinstance(command, app_commands.Group):
                for command in command.walk_commands():
                    walk_group_commands(base_group, command, staff_commands)
            elif _is_staff_command(command):
                staff_commands.append((command, base_group))

        for command in self.get_app_commands():
            if isinstance(command, app_commands.Group):
                walk_group_commands(command, command, staff_commands)
            elif _is_staff_command(command):
                staff_commands.append((command, None))

        return staff_commands

    def _add_cached_guild_staff_commands_info(
        self, guild: Snowflake, guild_app_commands: AppCommands
    ) -> None:
        cached_staff_commands = []
        for command, group in self._staff_commands:
            name = group.name if group else command.name
            if not (guild_app_command := guild_app_commands.get(name)):
                return
            cached_staff_commands.append(
                _CommandInfo(
                    guild_app_command.id,
                    command.qualified_name,
                    command.description,
                )
            )
        self._cached_guild_staff_commands_info[guild.id] = cached_staff_commands

    def _get_cached_guild_staff_commands_info(
        self, guild: Snowflake
    ) -> Optional[list[_CommandInfo]]:
        return self._cached_guild_staff_commands_info.get(guild.id)

    def _remove_cached_guild_staff_commands_info(self, guild: Snowflake) -> None:
        self._cached_guild_staff_commands_info.pop(guild.id, None)

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

    def _get_player(self, interaction: Interaction) -> Optional[IceBeatPlayer]:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        return self._bot.lavalink_client.player_manager.get(guild_id)  # pyright: ignore[reportReturnType]

    async def _disconnect_bot(
        self, player: IceBeatPlayer, voice_client: _LavalinkVoiceClient
    ) -> None:
        await player.stop()

        await voice_client.disconnect(force=True)

    async def _proceed_to_next_track(self, player: IceBeatPlayer) -> None:
        if not await self._guild_still_exists(player.guild_id):
            return

        try:
            await player.play()
        except Exception as e:
            __log__.warning(
                "Encountered an error when trying to play next enqueued track: %s",
                e,
            )

            await self._decide_bot_presence(player.guild_id)

    @override
    async def cog_unload(self) -> None:
        await self._lavalink_client.close()

    @tree_sync_listener(RegisteredAppCommands)
    async def on_registered_app_commands(self, event: RegisteredAppCommands) -> None:
        self._add_cached_guild_staff_commands_info(event.guild, event.commands)

    @tree_sync_listener(RemovedAppCommands)
    async def on_removed_app_commands(self, event: RemovedAppCommands) -> None:
        self._remove_cached_guild_staff_commands_info(event.guild)

    async def _try_warn_about_failed_track(
        self,
        track: Union[lavalink.AudioTrack, lavalink.DeferredAudioTrack],
    ) -> None:
        followup: Optional[Webhook] = track.extra.get("followup")
        if not followup:
            return

        track_link = _format_hyperlink(track.title, track.uri)
        embed = Embed(
            title="Sorry, I failed to play the track below",
            description=f"**{track_link}**",
            color=Color.yellow(),
        )
        try:
            await followup.send(embed=embed, ephemeral=True)
        except (HTTPException, NotFound):
            pass

    @lavalink.listener(lavalink.TrackStartEvent)
    async def on_track_start(self, event: lavalink.TrackStartEvent) -> None:
        await self._guild_still_exists(event.player.guild_id)

    @lavalink.listener(lavalink.TrackStuckEvent)
    async def on_track_stuck(self, event: lavalink.TrackStuckEvent) -> None:
        __log__.warning(
            "Track '%s' (%s) got stuck in server %d",
            event.track.title,
            event.track.uri,
            event.player.guild_id,
        )

        await self._try_warn_about_failed_track(event.track)

        await self._proceed_to_next_track(event.player)  # pyright: ignore[reportArgumentType]

    @lavalink.listener(lavalink.TrackExceptionEvent)
    async def on_track_exception(self, event: lavalink.TrackExceptionEvent) -> None:
        __log__.warning(
            "Track '%s' (%s) raised playback error in server %d: %s",
            event.track.title,
            event.track.uri,
            event.player.guild_id,
            event.cause,
        )

        await self._try_warn_about_failed_track(event.track)

        await self._proceed_to_next_track(event.player)  # pyright: ignore[reportArgumentType]

    @lavalink.listener(lavalink.TrackLoadFailedEvent)
    async def on_track_load_failed(self, event: lavalink.TrackLoadFailedEvent) -> None:
        __log__.warning(
            "Failed to load track '%s' (%s) in server %d: ",
            event.track.title,
            event.track.uri,
            event.player.guild_id,
            event.original or "track not playable",
        )

        await self._try_warn_about_failed_track(event.track)

        await self._proceed_to_next_track(event.player)  # pyright: ignore[reportArgumentType]

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
        player: Optional[IceBeatPlayer] = self._bot.lavalink_client.player_manager.get(
            member.guild.id
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

    @app_commands.command(description="Requests something to play")
    @app_commands.describe(
        query="Youtube/Spotify link or normal search as if you were on YouTube"
    )
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_ensure_player_is_ready()
    async def play(self, interaction: Interaction, query: str) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if player.queue.is_full():
            embed = Embed(title="Queue is full", color=Color.green())
            embed.set_footer(
                text=f"Queue only supports up to {player.queue.max_size} tracks"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            search = query if _URL_RE.match(query) else _QUERY_SEARCH_FMT.format(query)
            result = await player.node.get_tracks(search)
        except Exception as e:
            __log__.warning("Failed to request tracks: %v", e)

            embed = Embed(
                title="Search didn't proceed as expected",
                color=Color.green(),
            )
            embed.set_footer(text="I wasn't able to contact my assistant")
            await interaction.followup.send(embed=embed)

            return

        match result.load_type:
            case lavalink.LoadType.EMPTY:
                embed = Embed(
                    title="Sorry, I couldn't find anything to play", color=Color.green()
                )
                await interaction.followup.send(embed=embed)
                return
            case lavalink.LoadType.SEARCH | lavalink.LoadType.TRACK:
                tracks = result.tracks[:1]
            case lavalink.LoadType.PLAYLIST:
                tracks = result.tracks
            case lavalink.LoadType.ERROR:
                error: lavalink.LoadResultError = result.error  # pyright: ignore[reportAssignmentType]
                __log__.warning(
                    "Lavalink retrieved an error when trying to search '%s': %s",
                    search,
                    error.message,
                )

                embed = Embed(
                    title="I have no idea what you're looking for",
                    color=Color.green(),
                )
                embed.set_footer(text="What kind of voodoo shi you trying to do on me?")
                await interaction.followup.send(embed=embed)
                return

        free_queue_slots = player.queue.free_slots

        n_retrieved_tracks = len(tracks)
        n_enqueued_tracks = min(free_queue_slots, n_retrieved_tracks)
        for i in range(n_enqueued_tracks):
            track = tracks[i]
            track.extra["followup"] = interaction.followup
            player.add(track, requester=interaction.user.id)

        if len(tracks) == 1 and result.load_type != lavalink.LoadType.PLAYLIST:
            track = tracks[0]
            track_link = _format_hyperlink(track.title, track.uri)
            duration = _milli_to_human_readable(track.duration)
            embed = Embed(
                title="Track was enqueued",
                description=f"**{track_link}** ┃ `{duration}`",
                color=Color.green(),
            )
        else:
            collection_type = "Playlist"
            # LavaSrc plugin offers extra info for playlists: https://github.com/topi314/LavaSrc?tab=readme-ov-file#playlist
            if (
                result.plugin_info
                and (ptype := result.plugin_info.get("type"))
                and type(ptype) is str
            ):
                collection_type = ptype.capitalize()
            collection_link = _format_hyperlink(result.playlist_info.name, query)
            embed = Embed(
                title=f"{n_enqueued_tracks} track{'s' if free_queue_slots > 1 else ''} were enqueued",
                description=f"**{collection_type}:** **{collection_link}**",
                color=Color.green(),
            )
            if n_enqueued_tracks < n_retrieved_tracks:
                embed.set_footer(
                    text=f"It contains {n_retrieved_tracks} track"
                    f"{'s' if n_retrieved_tracks > 1 else ''}, although the queue has\n"
                    f"reached its full capacity ({player.queue.max_size} track"
                    f"{'s' if player.queue.max_size > 1 else ''})"
                )

        if not player.is_playing:
            await player.play()

        await interaction.followup.send(embed=embed)

    @play.autocomplete("query")
    @_is_whitelisted()
    @_bot_has_permissions(connect=True, speak=True)
    async def query_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if _URL_RE.match(current):
            return []

        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]
        try:
            player: IceBeatPlayer = self._bot.lavalink_client.player_manager.create(
                guild_id
            )
        except Exception as e:
            __log__.warning(
                "Failed to retrieve server player while processing "
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
        max_searches = min(len(tracks), _MAX_SEARCH_RESULTS)
        return [
            app_commands.Choice(name=tracks[i].title, value=tracks[i].uri)
            for i in range(max_searches)
        ]

    @app_commands.command(description="Stops the player")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_is_playing()
    @_ensure_player_is_ready()
    async def pause(self, interaction: Interaction) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

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
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_is_playing()
    @_ensure_player_is_ready()
    async def resume(self, interaction: Interaction) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

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
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_is_playing()
    @_ensure_player_is_ready()
    async def skip(self, interaction: Interaction) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        current_track: lavalink.AudioTrack = player.current  # pyright: ignore[reportAssignmentType]

        await player.skip()

        embed = Embed(
            title="Skipped current track",
            description=f"**I was playing [{current_track.title}]({current_track.uri})**",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Skips to a given queued track")
    @app_commands.describe(position="track position in queue")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_ensure_player_is_ready()
    async def jump(
        self,
        interaction: Interaction,
        position: app_commands.Range[int, 1, None],
    ) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

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
                    player.queue.shrink(position - 1)
                next_track = player.queue[0]

                await player.skip()

                embed = Embed(
                    title=f"Jumping to the {ordinal_position} track",
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
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_ensure_player_is_ready()
    async def pop(
        self,
        interaction: Interaction,
        position: app_commands.Range[int, 1, None],
    ) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

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
                    title=f"Successfully removed {ordinal_position} track from queue",
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
    @_is_whitelisted()
    @_bot_has_permissions(connect=True, speak=True)
    async def position_autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if not current.isdigit():
            return []

        player: Optional[IceBeatPlayer] = self._get_player(interaction)
        if not player:
            return []

        position = int(current)
        if not 1 <= position <= len(player.queue):
            return []

        track = player.queue[position - 1]

        return [app_commands.Choice(name=track.title, value=position)]

    @app_commands.command(description="Seeks to a given position in the track")
    @app_commands.describe(
        position="track position like in the YouTube video player, for example 5:38"
    )
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_is_playing()
    @_ensure_player_is_ready()
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
            player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

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
    @_default_user_permissions()
    @_is_whitelisted()
    @_bot_has_permissions(
        connect=True,
        speak=True,
    )
    @_cooldown()
    @_is_playing()
    @_ensure_player_is_ready()
    async def current(self, interaction: Interaction) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        def build_message(player: IceBeatPlayer) -> Embed:
            voice_client: _LavalinkVoiceClient = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
            current_track: lavalink.AudioTrack = player.current  # pyright: ignore[reportAssignmentType]
            position = player.position
            current_time = _milli_to_human_readable(position)
            timeline = ["─"] * _PLAYER_BAR_SIZE
            timeline[int((position * _PLAYER_BAR_SIZE) / current_track.duration)] = (
                ":white_circle:"
            )
            max_time = _milli_to_human_readable(current_track.duration)
            player_bar = f"`{current_time}` ┃{''.join(timeline)}┃ `{max_time}`"
            track_link = _format_hyperlink(current_track.title, current_track.uri)
            embed = Embed(
                title=f"Playing at <#{voice_client.channel.id}>"
                f"{' (paused)' if player.paused else ''}",
                description=f"**{track_link}**\n\n"
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
            return embed

        async def dispatch_message_edit() -> None:
            waiter: Optional[Waiter] = None
            try:
                # response = await interaction.original_response()
                msg_timeout = asyncio.create_task(
                    asyncio.sleep(_CURRENT_TRACK_MSG_TIMEOUT)
                )
                if player := self._get_player(interaction):
                    waiter = player.current_waiter()
                    while True:
                        edit_timeout = asyncio.create_task(
                            asyncio.sleep(_CURRENT_TRACK_MSG_EDIT_TIMEOUT)
                        )
                        wait_for_update = asyncio.create_task(waiter.wait())
                        done, _ = await asyncio.wait(
                            (msg_timeout, edit_timeout, wait_for_update),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if msg_timeout in done or not player.is_playing:
                            break
                        await interaction.edit_original_response(
                            embed=build_message(player)
                        )
                await interaction.delete_original_response()
            except (asyncio.CancelledError, HTTPException, ClientException, NotFound):
                pass
            finally:
                if waiter:
                    waiter.done()

        await interaction.response.send_message(
            embed=build_message(player), ephemeral=True
        )
        asyncio.create_task(dispatch_message_edit())

    @app_commands.command(description="Lists queued tracks")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_ensure_player_is_ready(bypass_channel_presence_check=True)
    async def queue(self, interaction: Interaction) -> None:
        pagination = InteractionPagination(
            _QUEUE_PAGINATION_TIMEOUT,
            _QueuePage(self._bot, interaction.guild),  # pyright: ignore[reportArgumentType]
            interaction,
        )
        await pagination.navigate()

    @app_commands.command(description="Removes all queued tracks")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_ensure_player_is_ready(bypass_channel_presence_check=True)
    async def clear(self, interaction: Interaction) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        if player.queue:
            player.queue.clear()

            embed = Embed(
                title="The queue is now empty",
                color=Color.green(),
            )
            ephemeral = False
        else:
            embed = Embed(
                title="There aren't queued tracks",
                color=Color.green(),
            )
            ephemeral = True
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @app_commands.command(description="Forces me to disconnect from the voice channel")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_ensure_player_is_ready()
    async def leave(self, interaction: Interaction) -> None:
        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]

        voice_client: _LavalinkVoiceClient = interaction.guild.voice_client  # pyright: ignore[reportOptionalMemberAccess, reportAssignmentType]
        await self._disconnect_bot(player, voice_client)

        embed = Embed(
            title=f"Disconnected from <#{voice_client.channel.id}>",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Toggles queue's shuffle mode")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_staff_only()
    @_is_guild_owner_or_staff()
    @_ensure_player_is_ready(bypass_channel_presence_check=True)
    async def shuffle(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        shuffle = await self._bot.store.switch_guild_shuffle(guild_id)

        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]
        player.set_shuffle(shuffle)

        embed = Embed(
            title=f"Shuffle mode has been {'enabled' if shuffle else 'disabled'}",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Toggles queue's loop mode")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_staff_only()
    @_is_guild_owner_or_staff()
    @_ensure_player_is_ready(bypass_channel_presence_check=True)
    async def loop(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        loop = await self._bot.store.switch_guild_shuffle(guild_id)

        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]
        player.set_loop(_parse_loop_mode(loop))

        embed = Embed(
            title=f"Loop mode has been {'enabled' if loop else 'disabled'}",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Changes player volume")
    @app_commands.describe(level="volume level (the higher, the worst)")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_staff_only()
    @_is_guild_owner_or_staff()
    @_ensure_player_is_ready(bypass_channel_presence_check=True)
    async def volume(
        self, interaction: Interaction, level: app_commands.Range[int, 0, 100]
    ) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        await self._bot.store.set_guild_volume(guild_id, volume=level)

        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]
        await player.set_volume(vol=level)

        embed = Embed(title="Volume has been changed", color=Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Sets player filter")
    @app_commands.rename(filter="name")
    @app_commands.describe(filter="filter name")
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_staff_only()
    @_is_guild_owner_or_staff()
    @_ensure_player_is_ready(bypass_channel_presence_check=True)
    async def filter(
        self,
        interaction: Interaction,
        filter: Filter,
    ) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        await self._bot.store.set_guild_filter(guild_id, filter)

        player: IceBeatPlayer = self._get_player(interaction)  # pyright: ignore[reportAssignmentType]
        await _set_filter_preset(player, filter)

        embed = Embed(
            title=f"Filter has been changed to {filter.name}", color=Color.green()
        )
        await interaction.response.send_message(embed=embed)

    _presence_group = app_commands.Group(
        name="presence",
        description="Decides bot behaviour when queue is empty (the bot leaves the voice channel when it's alone)",
        guild_only=True,
        default_permissions=_DEFAULT_USER_PERMISSIONS,
    )

    @_presence_group.command(
        name="stay",
        description="Bot won’t leave the voice channel when the queue's empty",
    )
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_staff_only()
    @_is_guild_owner_or_staff()
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
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_staff_only()
    @_is_guild_owner_or_staff()
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

    _staff_group = app_commands.Group(
        name="staff",
        description="Manages server staff role",
        guild_only=True,
        default_permissions=_DEFAULT_USER_PERMISSIONS,
    )

    @_staff_group.command(
        name="set",
        description="Sets staff role (additional users are allowed to configure the player)",
    )
    @app_commands.describe(role="staff role")
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_is_guild_owner()
    async def staff_set(self, interaction: Interaction, role: Role) -> None:
        await self._bot.store.set_guild_staff_role_id(
            interaction.guild_id,  # pyright: ignore[reportArgumentType]
            role.id,
        )

        embed = Embed(
            title="Staff has been changed",
            description=f"**Role:** <@&{role.id}>",
            color=Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @_staff_group.command(
        name="unset",
        description="Removes staff role (only the server owner will be allowed to configure the player)",
    )
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    @_is_guild_owner()
    async def staff_unset(self, interaction: Interaction) -> None:
        guild_id: int = interaction.guild_id  # pyright: ignore[reportAssignmentType]

        guild_db = await self._bot.store.get_guild(guild_id)
        if guild_db.staff_role_id:
            await self._bot.store.unset_guild_staff_role_id_if_same(
                guild_id, guild_db.staff_role_id
            )

        embed = Embed(title="Staff role has been removed", color=Color.green())
        await interaction.response.send_message(embed=embed)

    @_staff_group.command(
        name="commands",
        description="Lists staff commands",
    )
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
    async def staff_commands(self, interaction: Interaction) -> None:
        if commands := self._get_cached_guild_staff_commands_info(interaction.guild):  # pyright: ignore[reportArgumentType]
            fmted_commands = "\n".join(
                f"┌ </{command.qualified_name}:{command.id}>\n└ {command.description}"
                for command in commands
            )
            embed = Embed(
                title="Staff Commands",
                description=fmted_commands,
                color=Color.green(),
            )
            embed.set_footer(text="Server owner can also use these commands")
        else:
            embed = Embed(
                title="I was unable to find staff commands",
                color=Color.green(),
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Displays player info")
    @app_commands.guild_only()
    @_default_user_permissions()
    @_is_whitelisted()
    @_cooldown()
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
        staff_role = "not assigned"
        if guild_db.staff_role_id:
            if interaction.guild.get_role(guild_db.staff_role_id):  # pyright: ignore[reportOptionalMemberAccess]
                staff_role = f"<@&{guild_db.staff_role_id}>"
            else:
                await self._bot.store.unset_guild_staff_role_id_if_same(
                    guild_id, guild_db.staff_role_id
                )
        embed.add_field(
            name="┃ Filter :level_slider:",
            value=f"- {guild_db.filter.name}",
        )
        embed.add_field(
            name="┃ Player Internal Volume :sound:",
            value=f"- {guild_db.volume}",
            inline=False,
        )
        embed.add_field(
            name="┃ Shuffle Mode :twisted_rightwards_arrows:",
            value=f"- {shuffle_mode_state}",
        )
        embed.add_field(
            name="┃ Loop Mode :arrows_counterclockwise:",
            value=f"- {loop_mode_state}",
            inline=False,
        )
        embed.add_field(
            name="┃ When Queue is Empty :zzz:",
            value=f"- {bot_presence}",
        )
        embed.add_field(name="┃ State :notes:", value=f"- {player_state}", inline=False)
        embed.add_field(name="┃ Staff Role :technologist:", value=f"- {staff_role}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @override
    async def cog_app_command_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, (HTTPException, NotFound)):
            return
        elif isinstance(error, _GuildNotWhitelisted):
            embed = Embed(
                title="This server isn't whitelisted",
                color=Color.yellow(),
            )
        elif isinstance(error, (_NotGuildOwner, _NotGuildOwnerNorStaff)):
            embed = Embed(
                title="This command has restricted access",
                description="**Allowed users:** server owner",  # pyright: ignore[reportOptionalMemberAccess]
                color=Color.yellow(),
            )
            if isinstance(error, _NotGuildOwnerNorStaff) and error.staff_role_id:
                embed.description = (
                    f"{embed.description} and members of role <@&{error.staff_role_id}>"
                )
        elif isinstance(error, app_commands.BotMissingPermissions):
            embed = Embed(
                title="Some permissions for me are missing",
                color=Color.yellow(),
            )
            fmted_perms = _prettify_missing_bot_permissions(error)
            if isinstance(error, _BotMissingPermissionsInVoiceChannel):
                description = (
                    f"**I'm not allowed to** {fmted_perms} "
                    f"**in** <#{error.voice_channel_id}>"
                )
            elif isinstance(error, _BotRoleMissingPermissionsInVoiceChannel):
                description = (
                    f"**Bot role <@&{error.role_id}> doesn't allow to** "
                    f"{fmted_perms} **in** <#{error.voice_channel_id}>"
                )
            else:
                description = f"**I can't** {fmted_perms}"
            embed.description = description
        elif isinstance(error, app_commands.CommandOnCooldown):
            embed = Embed(
                title="Take it easy, do not spam commands",
                color=Color.yellow(),
            )
            embed.set_footer(text="You still have tomorrow")
        elif isinstance(error, _FailedToRetrievePlayer):
            __log__.warning(
                "Failed to retrieve server player: %v", error.original_error
            )

            embed = Embed(
                title="Music player failed to start",
                color=Color.yellow(),
            )
            embed.set_footer(text="Sorry, but something went wrong...")
        elif isinstance(error, _FailedToPreparePlayer):
            __log__.warning("Failed to prepare server player: %v", error.original_error)

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
                embed.description = f"Hop into <#{bot_voice_channel.id}>, I'm here"
        elif isinstance(error, _BotNotInVoiceChannel):
            embed = Embed(
                title="I'm not in a voice channel",
                color=Color.yellow(),
            )
        elif isinstance(error, _DifferentVoiceChannels):
            embed = Embed(
                title="You aren't in my voice channel",
                color=Color.yellow(),
            )
            embed.description = f"Come to <#{error.voice_channel_id}>"
        elif isinstance(error, _VoiceChannelIsFull):
            embed = Embed(
                title="Damn son, the channel's overflowing",
                color=Color.yellow(),
            )
            embed.set_footer(text="I mean, it's full... duh")
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
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except HTTPException:
            pass
