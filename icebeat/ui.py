from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
import logging
from typing import Optional
from discord import Button, ButtonStyle, Color, Embed, Interaction
from discord.ext import commands
from discord.ui import Item, View, button


__all__ = ["FetchPage", "ContextPagination", "InteractionPagination"]

__log__ = logging.getLogger(__name__)


FetchPage = Callable[[int], Coroutine[None, None, tuple[Embed, int, int]]]


class _BasePagination(ABC, View):
    __slots__ = ("_delete_after", "_fetch_page", "_current_page", "_total_pages")

    def __init__(
        self, timeout: float, delete_after: Optional[float], fetch_page: FetchPage
    ):
        super().__init__(timeout=timeout)

        self._delete_after = delete_after
        self._fetch_page = fetch_page
        self._current_page = 1
        self._total_pages = 1

    @abstractmethod
    async def _send_message(
        self,
        *,
        embed: Embed,
        view: Optional[View] = None,
        delete_after: Optional[float],
    ) -> None: ...

    @abstractmethod
    async def _edit_message(self, *, embed: Embed, view: View) -> None: ...

    def _update_buttons(self):
        self.children[0].disabled = self._current_page == 1  # pyright: ignore[reportAttributeAccessIssue]
        self.children[1].disabled = self._current_page == self._total_pages  # pyright: ignore[reportAttributeAccessIssue]

    async def _edit_page(self, interaction: Interaction):
        emb, self._current_page, self._total_pages = await self._fetch_page(
            self._current_page
        )

        self._update_buttons()

        await interaction.response.edit_message(embed=emb, view=self)

    @button(label="Previous", style=ButtonStyle.gray)  # pyright: ignore[reportArgumentType]
    async def previous(self, interaction: Interaction, button: Button) -> None:
        _ = button

        self._current_page -= 1

        await self._edit_page(interaction)

    @button(label="Next", style=ButtonStyle.gray)  # pyright: ignore[reportArgumentType]
    async def next(self, interaction: Interaction, button: Button) -> None:
        _ = button

        self._current_page += 1

        await self._edit_page(interaction)

    async def on_error(
        self, interaction: Interaction, error: Exception, item: Item, /
    ) -> None:
        _ = item

        __log__.warning(f"Paginated view has failed: {error}")

        embed = Embed(title="Something unexpected has happened", color=Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

    async def navigate(self):
        embed, self._current_page, self._total_pages = await self._fetch_page(
            self._current_page
        )

        if self._total_pages == 1:
            await self._send_message(embed=embed, delete_after=self._delete_after)
        elif self._total_pages > 1:
            self._update_buttons()

            await self._send_message(
                embed=embed, view=self, delete_after=self._delete_after
            )

    @staticmethod
    def compute_total_pages(total_elements: int, elements_per_page: int) -> int:
        return ((total_elements - 1) // elements_per_page) + 1


class ContextPagination(_BasePagination):
    __slots__ = ("_ctx", "_msg")

    def __init__(
        self,
        timeout: float,
        fetch_page: FetchPage,
        ctx: commands.Context,
        delete_after: Optional[float] = None,
    ) -> None:
        super().__init__(timeout, delete_after, fetch_page)

        self._ctx = ctx

    async def interaction_check(self, interaction: Interaction) -> bool:
        return self._ctx.author.id == interaction.user.id

    async def on_timeout(self):
        await self._msg.edit(view=None)

    async def _send_message(
        self,
        *,
        embed: Embed,
        view: Optional[View] = None,
        delete_after: Optional[float] = None,
    ) -> None:
        self._msg = await self._ctx.reply(
            embed=embed,
            view=view,  # pyright: ignore[reportArgumentType, reportCallIssue]
            ephemeral=True,
            delete_after=delete_after,  # pyright: ignore[reportArgumentType]
        )

    async def _edit_message(self, *, embed: Embed, view: View) -> None:
        await self._msg.edit(embed=embed, view=view)


class InteractionPagination(_BasePagination):
    __slots__ = ("_interaction",)

    def __init__(
        self,
        timeout: float,
        fetch_page: FetchPage,
        interaction: Interaction,
        delete_after: Optional[float] = None,
    ) -> None:
        super().__init__(timeout, delete_after, fetch_page)

        self._interaction = interaction

    async def interaction_check(self, interaction: Interaction) -> bool:
        return self._interaction.user.id == interaction.user.id

    async def on_timeout(self):
        original_response = await self._interaction.original_response()
        await original_response.edit(view=None)

    async def _send_message(
        self,
        *,
        embed: Embed,
        view: Optional[View] = None,
        delete_after: Optional[float],
    ) -> None:
        await self._interaction.response.send_message(
            embed=embed,
            view=view,  # pyright: ignore[reportArgumentType, reportCallIssue]
            ephemeral=True,
            delete_after=delete_after,  # pyright: ignore[reportArgumentType]
        )

    async def _edit_message(self, *, embed: Embed, view: View) -> None:
        await self._interaction.response.edit_message(embed=embed, view=view)
