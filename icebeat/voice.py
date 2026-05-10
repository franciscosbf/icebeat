__all__ = ["LavalinkVoiceClient"]

import logging
from typing import override

from discord import Client, VoiceChannel, VoiceProtocol
from discord.abc import Connectable
from discord.types.voice import (
    GuildVoiceState as GuildVoiceStatePayload,
    VoiceServerUpdate as VoiceServerUpdatePayload,
)
import lavalink

from icebeat.player import IceBeatPlayer

__log__ = logging.getLogger(__name__)


class LavalinkVoiceClient(VoiceProtocol):
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
    async def disconnect(self, *, force: bool = True, stop: bool = False) -> None:
        player: IceBeatPlayer = self._lavalink_client.player_manager.get(self._guild.id)  # pyright: ignore[reportAssignmentType]

        if not force and not player.is_connected:  # pyright: ignore[reportOptionalMemberAccess]
            return

        if stop:
            try:
                await player.stop()
            except Exception as e:
                __log__.warning("Failed to request Lavalink to stop player %s", e)

        await self._guild.change_voice_state(channel=None)

        player.channel_id = None  #  pyright: ignore[reportOptionalMemberAccess]
        await self._destroy()
