import logging
from typing import TYPE_CHECKING, Any, Callable

from discord import Color, Embed, Guild
from discord.ext import commands

from ..ui import ContextPagination, Page, compute_total_pages


if TYPE_CHECKING:
    from ..bot import IceBeat

__all__ = ["Owner"]

__log__ = logging.getLogger(__name__)

_WHITELIST_VIEW_TIMEOUT = 60.0
_WHITELIST_VIEW_PAGE_SIZE = 6


def _cooldown() -> Callable[[commands.core.T], commands.core.T]:
    def cooldown(ctx: commands.Context) -> commands.Cooldown:
        bot: "IceBeat" = ctx.bot

        return commands.Cooldown(
            rate=bot.cooldown_preset.rate, per=bot.cooldown_preset.time
        )

    return commands.dynamic_cooldown(cooldown=cooldown, type=commands.BucketType.guild)


def _command_extras(**kwargs) -> dict[str, Any]:
    return kwargs


class _SubcommandNotFound(commands.CommandError):
    pass


class _WhitelistPage(Page):
    __slots__ = ("_bot", "_whitelist_waiter")

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot
        self._whitelist_waiter = bot.store.whitelist_waiter()

    async def fetch(self, current_page: int) -> tuple[Embed, int, int]:
        whitelist = await self._bot.store.get_whitelist()
        if not whitelist.guild_ids:
            embed = Embed(
                title="There aren't whitelisted servers",
                color=Color.green(),
            )
            return embed, 1, 1
        total_pages = compute_total_pages(
            len(whitelist.guild_ids), _WHITELIST_VIEW_PAGE_SIZE
        )
        if current_page > total_pages:
            current_page = total_pages
        offset = (current_page - 1) * _WHITELIST_VIEW_PAGE_SIZE
        guilds = []
        for guild_id in list(whitelist.guild_ids)[
            offset : offset + _WHITELIST_VIEW_PAGE_SIZE
        ]:
            if guild := self._bot.get_guild(guild_id):
                guilds.append(guild)
                continue

            await self._bot.store.remove_from_whitelist(guild_id)

            __log__.info(
                "Removed server %s from whitelist as bot is no longer a member",
                guild_id,
            )

            return await self.fetch(current_page)

        embed = Embed(
            title="Whitelisted Servers",
            description="\n".join(
                f'- **"{guild.name}"** (**{guild.id}**)' for guild in guilds
            ),
            color=Color.green(),
        )
        embed.set_footer(text=f"page {current_page}/{total_pages}")
        return embed, current_page, total_pages

    def unavailable_page_alert(self) -> Embed:
        return Embed(
            title="Whitelist no longer available",
            description=f"Type **/{Owner.whitelist_show.qualified_name}** to list whitelisted servers",
            color=Color.green(),
        )

    async def wait_for_edit_request(self) -> None:
        await self._whitelist_waiter.wait()

    def cancel_edit_request(self) -> None:
        self._whitelist_waiter.done()


class Owner(commands.Cog):
    __slots__ = ("_bot",)

    def __init__(self, bot: "IceBeat") -> None:
        self._bot = bot

    @commands.group()
    @_cooldown()
    @commands.is_owner()
    @commands.dm_only()
    async def whitelist(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand:
            return

        if ctx.subcommand_passed:
            raise _SubcommandNotFound()

        embed = Embed(
            title="Available Subcommands",
            description=f"**Usage:** {ctx.prefix if ctx.prefix else ''}{self.whitelist.name} <subcommand> <arguments>",
            color=Color.green(),
        )
        for subcommand in self.whitelist.all_commands.values():
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
        await ctx.reply(embed=embed)

    @whitelist.command(
        name="show",
        description="Displays whitelisted servers",
        extras=_command_extras(emoji=":clipboard:"),
    )
    async def whitelist_show(self, ctx: commands.Context) -> None:
        pagination = ContextPagination(
            _WHITELIST_VIEW_TIMEOUT,
            _WhitelistPage(self._bot),
            ctx,
        )
        await pagination.navigate()

    @whitelist.command(
        name="add",
        description="Whitelists a server",
        extras=_command_extras(emoji=":flag_white:"),
    )
    async def whitelist_add(self, ctx: commands.Context, server: Guild) -> None:
        inserted = await self._bot.store.add_to_whitelist(server.id)
        if inserted:
            await self._bot.add_app_commands_to_guild(server)

            embed = Embed(
                title=f'Server "{server.name}" was inserted into the whitelist',
                color=Color.green(),
            )
        else:
            embed = Embed(
                title=f'Server "{server.name}" is already whitelisted',
                color=Color.yellow(),
            )
        embed.set_footer(text=f"Server ID: {server.id}")
        await ctx.reply(embed=embed)

    @whitelist.command(
        name="remove",
        description="Removes a server from the whitelist",
        extras=_command_extras(emoji=":flag_black:"),
    )
    async def whitelist_remove(self, ctx: commands.Context, server: Guild) -> None:  # pyright: ignore[reportArgumentType]
        blacklisted = await self._bot.store.remove_from_whitelist(server.id)

        if blacklisted:
            await self._bot.remove_app_commands_from_guild(server)

            embed = Embed(
                title=f'Server "{server.name}" was removed from the whitelist',
                color=Color.green(),
            )
        else:
            embed = Embed(
                title=f'Server "{server.name}" isn\'t whitelisted', color=Color.yellow()
            )
        embed.set_footer(text=f"Server ID: {server.id}")
        await ctx.reply(embed=embed)

    @whitelist.command(
        name="sync",
        description="Updates slash commands for a whitelisted server or synchronizes them globally",
        extras=_command_extras(emoji=":satellite_orbital:"),
    )
    async def whitelist_sync(
        self,
        ctx: commands.Context,
        server: Guild = None,  # pyright: ignore[reportArgumentType]
    ) -> None:
        whitelist = await self._bot.store.get_whitelist()
        if not server:
            if whitelist.guild_ids:
                for guild_id in whitelist.guild_ids:
                    if guild := self._bot.get_guild(guild_id):
                        await self._bot.add_app_commands_to_guild(guild)
                    else:
                        await self._bot.store.remove_from_whitelist(guild_id)

                        await self._bot.remove_app_commands_from_guild(guild)

                        __log__.info(
                            "Removed server %s from whitelist as bot is no longer a member",
                            guild_id,
                        )

                embed = Embed(
                    title="Commands synced with success on all servers",
                    color=Color.green(),
                )
            else:
                embed = Embed(
                    title="Whitelist is empty",
                    color=Color.yellow(),
                )
        else:
            if server.id in whitelist.guild_ids:
                await self._bot.add_app_commands_to_guild(server)

                embed = Embed(
                    title=f'Commands synced with success on server "{server.name}"',
                    color=Color.green(),
                )
            else:
                embed = Embed(
                    title=f'Server "{server.name}" isn\'t whitelisted',
                    color=Color.yellow(),
                )
            embed.set_footer(text=f"Server ID: {server.id}")
        await ctx.reply(embed=embed)

    @whitelist.error
    @whitelist_show.error
    @whitelist_add.error
    @whitelist_remove.error
    @whitelist_sync.error
    async def whitelist_command_error(
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
            __log__.warning(
                f"Error on {ctx.command.qualified_name} command",  # pyright: ignore[reportOptionalMemberAccess]
                exc_info=True,
            )

            embed = Embed(
                title="Something unexpected went wrong...",
                color=Color.red(),
            )
        await ctx.reply(embed=embed)
