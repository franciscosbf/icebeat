import logging
from typing import TYPE_CHECKING
from discord import Color, Embed, Interaction, Permissions, app_commands
from discord.ext import commands

from ..model import Filter

if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Music"]

_PERMISSIONS = Permissions(connect=True, speak=True, send_messages=True)


__log__ = logging.getLogger(__name__)


class _GuildNotWhitelisted(app_commands.CheckFailure):
    pass


class _NotGuildOwner(app_commands.CheckFailure):
    pass


def _is_whitelisted():
    async def predicate(interaction: Interaction) -> bool:
        bot: "IceBeat" = interaction.client  # pyright: ignore[reportAssignmentType]

        whitelist = await bot.store.get_whitelist()
        if interaction.guild_id in whitelist.guild_ids:  # pyright: ignore[reportOptionalMemberAccess]
            return True
        raise _GuildNotWhitelisted()

    return app_commands.check(predicate)


def _is_guild_owner():
    def predicate(interaction: Interaction) -> bool:
        if interaction.user.id == interaction.guild.owner_id:  # pyright: ignore[reportOptionalMemberAccess]
            return True
        raise _NotGuildOwner()

    return app_commands.check(predicate)


class Music(commands.Cog):
    __slots__ = ("_bot",)

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot

    @app_commands.command(description="player whatever you want")
    @app_commands.describe(search="url or as if you were searching on YouTube")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    async def play(self, interaction: Interaction, search: str) -> None:
        _, _ = interaction, search
        pass  # TODO: implement

    @app_commands.command(description="stops the player")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    async def pause(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="resumes the player")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    async def resume(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="skips what's currently playing")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    async def skip(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="displays queue")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    async def queue(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="enables or disables shuffle mode")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    async def shuffle(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(description="enables or disables shuffle mode")
    @app_commands.describe(level="volume level (the higher, the worst)")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    @_is_guild_owner()
    async def volume(
        self, interaction: Interaction, level: app_commands.Range[int, 0, 1000]
    ) -> None:
        _, _ = interaction, level
        pass  # TODO: implement

    @app_commands.command(description="sets player filter")
    @app_commands.describe(name="filter name")
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    @_is_guild_owner()
    async def filter(self, interaction: Interaction, name: Filter) -> None:
        _, _ = interaction, name
        pass  # TODO: implement

    @app_commands.command(
        description="bot won't leave the voice channel if queue gets empty"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    @_is_guild_owner()
    async def stay(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(
        description="bot will remain in the voice channel if queue gets empty"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    @_is_guild_owner()
    async def autoleave(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(
        description="if a normal search is provided to play, the bot will select the first result"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    @_is_guild_owner()
    async def autosearch(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

    @app_commands.command(
        description="if a normal search is provided to play, you will be able to select between multiple results"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(_PERMISSIONS)
    @_is_whitelisted()
    @_is_guild_owner()
    async def selectsearch(self, interaction: Interaction) -> None:
        _ = interaction
        pass  # TODO: implement

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
        else:
            __log__.warning(
                f"Error on {interaction.command.name} command",  # pyright: ignore[reportOptionalMemberAccess]
                exc_info=True,
            )

            embed = Embed(
                title="Something unexpected went wrong...",
                color=Color.red(),
            )

        await interaction.response.send_message(embed=embed)
