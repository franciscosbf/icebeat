import logging
from typing import TYPE_CHECKING
from discord import Permissions, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Music"]

_PERMISSIONS = Permissions(
    connect=True, speak=True, send_messages=True, manage_messages=True
)


__log__ = logging.getLogger(__name__)


@app_commands.guild_only()
@app_commands.default_permissions(_PERMISSIONS)
class Music(commands.GroupCog, group_name="player", description="Player Interactions"):
    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot
