import logging
from typing import TYPE_CHECKING, Any, Callable

from discord import Color, Embed, Guild
import discord
from discord.ext import commands

from ..ui import ContextPagination


if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Owner"]

__log__ = logging.getLogger(__name__)

_WHITELIST_VIEW_TIMEOUT = 20.0
_WHITELIST_VIEW_PAGE_SIZE = 6


def _cooldown() -> Callable[[commands.core.T], commands.core.T]:
    return commands.cooldown(rate=2, per=4.0, type=commands.BucketType.guild)


def _command_extras(**kwargs) -> dict[str, Any]:
    return kwargs


class _SubcommandNotFound(commands.CommandError):
    pass


class Owner(commands.Cog):
    __slots__ = ("_bot",)

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot

    @commands.group()
    @_cooldown()
    @commands.is_owner()
    @commands.dm_only()
    async def owner(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand:
            return

        if ctx.subcommand_passed:
            raise _SubcommandNotFound()

        embed = Embed(
            title="Available Subcommands",
            description=f"**Usage:** {ctx.prefix if ctx.prefix else ''}{self.owner.name} <subcommand> <arguments>",
            color=Color.green(),
        )
        for subcommand in self.owner.all_commands.values():
            parameters = " ".join(
                f"<{parameter.name}{'' if parameter.required else ' (optional)'}>"
                for parameter in subcommand.clean_params.values()
            )
            embed.add_field(
                name=f"{subcommand.extras['emoji']} ┃ {subcommand.name} {parameters}",
                value=f"- {subcommand.description}",
                inline=False,
            )
        embed.set_footer(
            text='"server" parameter can be either its name or ID (the latter is preferred)'
        )
        await ctx.send(embed=embed)

    @owner.command(
        description="Whitelists a server or displays whitelisted servers",
        extras=_command_extras(emoji=":flag_white:"),
    )
    async def whitelist(self, ctx: commands.Context, server: Guild = None) -> None:  # pyright: ignore[reportArgumentType]
        async def fetch_whitelist_page(current_page: int) -> tuple[Embed, int]:
            whitelist = await self._bot.store.get_whitelist()
            if not whitelist.guild_ids:
                embed = Embed(
                    title="There aren't whitelisted servers",
                    color=Color.green(),
                )
                return embed, 1
            offset = (current_page - 1) * _WHITELIST_VIEW_PAGE_SIZE
            guild_previews = []
            for guild_id in list(whitelist.guild_ids)[
                offset : offset + _WHITELIST_VIEW_PAGE_SIZE
            ]:
                try:
                    guild_preview = await self._bot.fetch_guild_preview(guild_id)
                except discord.NotFound:
                    await self._bot.store.remove_from_whitelist(guild_id)

                    __log__.info(
                        "Removed server %s from whitelist as bot is no longer a member",
                        guild_id,
                    )

                    total_pages = ContextPagination.compute_total_pages(
                        len(whitelist.guild_ids) - 1, _WHITELIST_VIEW_PAGE_SIZE
                    )
                    if total_pages < current_page:
                        current_page = total_pages
                    return await fetch_whitelist_page(current_page)
                else:
                    guild_previews.append(guild_preview)
            embed = Embed(
                title="Whitelisted Servers:",
                description="\n".join(
                    f"**{guild_preview.name}** (ID: **{guild_preview.id}**)"
                    for guild_preview in guild_previews
                ),
                color=Color.green(),
            )
            total_pages = ContextPagination.compute_total_pages(
                len(whitelist.guild_ids), _WHITELIST_VIEW_PAGE_SIZE
            )
            embed.set_footer(text=f"page {current_page}/{total_pages}")
            return embed, total_pages

        if server:
            inserted = await self._bot.store.add_to_whitelist(server.id)
            embed = Embed(color=Color.green())
            if inserted:
                embed.title = f'Server "{server.name}" was inserted into the whitelist'
            else:
                embed.title = f'Server "{server.name}" is already whitelisted'
            embed.set_footer(text=f"Server ID: {server.id}")
            await ctx.send(embed=embed)

            await self._bot.add_app_commands_to_guild(server)

            return

        pagination = ContextPagination(
            _WHITELIST_VIEW_TIMEOUT, fetch_whitelist_page, ctx
        )
        await pagination.navigate()

    @owner.command(
        description="Removes a server from the whitelist",
        extras=_command_extras(emoji=":flag_black:"),
    )
    async def blacklist(self, ctx: commands.Context, server: Guild) -> None:  # pyright: ignore[reportArgumentType]
        blacklisted = await self._bot.store.remove_from_whitelist(server.id)

        embed = Embed(color=Color.green())
        if blacklisted:
            embed = Embed(
                title=f'Server "{server.name}" was removed from the whitelist',
                color=Color.green(),
            )
        else:
            embed = Embed(
                title=f'Server "{server.name}" isn\'t whitelisted', color=Color.yellow()
            )
        embed.set_footer(text=f"Server ID: {server.id}")
        await ctx.send(embed=embed)

        await self._bot.remove_app_commands_from_guild(server)

    @owner.command(
        description="Updates slash commands of a whitelisted server",
        extras=_command_extras(emoji=":satellite_orbital:"),
    )
    async def sync(self, ctx: commands.Context, server: Guild) -> None:
        whitelist = await self._bot.store.get_whitelist()
        if server.id in whitelist.guild_ids:
            await self._bot.add_app_commands_to_guild(server)

            embed = Embed(
                title=f'Commands synced with success on server "{server.name}"',
                color=Color.green(),
            )
        else:
            embed = Embed(
                title=f'Server "{server.name}" isn\'t whitelisted', color=Color.yellow()
            )
        embed.set_footer(text=f"Server ID: {server.id}")
        await ctx.send(embed=embed)

    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: Exception,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            embed = Embed(
                title="Invalid server name/ID or bot isn't a member of it",
                color=Color.yellow(),
            )
        elif isinstance(error, commands.CommandOnCooldown):
            embed = Embed(
                title="You need to take it easy, slow down",
                color=Color.yellow(),
            )
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = Embed(
                title="You must provide a server name or ID",
                color=Color.yellow(),
            )
            embed.set_footer(text="I may not differentiate servers only by its name")
        elif isinstance(error, (commands.PrivateMessageOnly, commands.NotOwner)):
            return
        elif isinstance(error, _SubcommandNotFound):
            embed = Embed(
                title=f'No subcommand named "{ctx.subcommand_passed}"',
                color=Color.yellow(),
            )
        else:
            __log__.warning(f"Error on {ctx.command.name} command", exc_info=True)  # pyright: ignore[reportOptionalMemberAccess]

            embed = Embed(
                title="Something unexpected went wrong...",
                color=Color.red(),
            )
        await ctx.send(embed=embed)
