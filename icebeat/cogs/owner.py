import logging
from typing import TYPE_CHECKING

from discord import Color, Embed, Guild
import discord
from discord.ext import commands

from icebeat.ui import ContextPagination


if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Owner"]

_COOLDOWN_RATE = 2
_COOLDOWN_PER = 4.0
_WHITELIST_VIEW_TIMEOUT = 20.0
_WHITELIST_VIEW_PAGE_SIZE = 6

__log__ = logging.getLogger(__name__)


class Owner(commands.Cog):
    __slots__ = ("_bot",)

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot

    @commands.command()
    @commands.dm_only()
    @commands.is_owner()
    @commands.cooldown(_COOLDOWN_RATE, _COOLDOWN_PER)
    async def whitelist(self, ctx: commands.Context, guild: Guild = None) -> None:  # pyright: ignore[reportArgumentType]
        async def fetch_whitelist_page(current_page: int) -> tuple[Embed, int]:
            whitelist = await self._bot.store.get_whitelist()
            if not whitelist.guild_ids:
                embed = Embed(
                    title="There aren't whitelisted servers",
                    description="Use !whitelist <server name or ID> to add a server",
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

        if guild:
            inserted = await self._bot.store.add_to_whitelist(guild.id)
            embed = Embed(color=Color.green())
            if inserted:
                embed.description = f"Server **{guild.name}** (ID: **{guild.id}**) was inserted into the whitelist"
            else:
                embed.description = f"Server **{guild.name}** (ID: **{guild.id}**) is already whitelisted"
            await ctx.send(embed=embed)

            return

        pagination = ContextPagination(
            _WHITELIST_VIEW_TIMEOUT, fetch_whitelist_page, ctx
        )
        await pagination.navigate()

    @commands.command()
    @commands.dm_only()
    @commands.is_owner()
    @commands.cooldown(_COOLDOWN_RATE, _COOLDOWN_PER)
    async def blacklist(self, ctx: commands.Context, guild: Guild) -> None:  # pyright: ignore[reportArgumentType]
        blacklisted = await self._bot.store.remove_from_whitelist(guild.id)
        embed = Embed(color=Color.green())
        if blacklisted:
            embed.description = f"Server **{guild.name}** (ID: **{guild.id}**) was removed from the whitelist"
        else:
            embed.description = (
                f"Server **{guild.name}** (ID: **{guild.id}**) isn't whitelisted"
            )
        await ctx.send(embed=embed)

    @whitelist.error
    @blacklist.error
    async def whitelist_and_blacklist_error_handler(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.BadArgument):
            embed = Embed(
                title="Invalid server ID or bot isn't a member", color=Color.yellow()
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
            embed.set_footer(
                text="Note: I may not differentiate servers only by its name"
            )
        elif isinstance(error, (commands.PrivateMessageOnly, commands.NotOwner)):
            return
        else:
            __log__.warning(f"Error on {ctx.command.name} command", exc_info=True)  # pyright: ignore[reportOptionalMemberAccess]

            embed = Embed(
                title="Something unexpected went wrong...",
                color=Color.red(),
            )

        await ctx.send(embed=embed)
