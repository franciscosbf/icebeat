from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
import logging
from typing import Optional
from discord import Button, ButtonStyle, Color, Embed, Interaction
from discord.ext import commands
from discord.ui import Item, View, button


__all__ = ["FetchPage", "ContextPagination", "InteractionPagination"]

__log__ = logging.getLogger(__name__)


FetchPage = Callable[[int], Coroutine[None, None, tuple[Embed, int]]]


class _BasePagination(ABC, View):
    __slots__ = ("_fetch_page", "_current_page", "_total_pages")

    def __init__(self, timeout: float, fetch_page: FetchPage):
        super().__init__(timeout=timeout)

        self._fetch_page = fetch_page
        self._current_page = 1
        self._total_pages = 1

    @abstractmethod
    async def _send_message(
        self, *, embed: Embed, view: Optional[View] = None
    ) -> None: ...

    @abstractmethod
    async def _edit_message(self, *, embed: Embed, view: View) -> None: ...

    def _update_buttons(self):
        self.children[0].disabled = self._current_page == 1  # pyright: ignore[reportAttributeAccessIssue]
        self.children[1].disabled = self._current_page == self._total_pages  # pyright: ignore[reportAttributeAccessIssue]

    async def _edit_page(self, interaction: Interaction):
        emb, self._total_pages = await self._fetch_page(self._current_page)

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
        embed, self._total_pages = await self._fetch_page(self._current_page)
        assert self._total_pages > 0, (
            "total_pages returned by fetch_page must be greater than zero"
        )
        if self._total_pages == 1:
            await self._send_message(embed=embed)
        elif self._total_pages > 1:
            self._update_buttons()

            await self._send_message(embed=embed, view=self)

    @staticmethod
    def compute_total_pages(total_elements: int, elements_per_page: int) -> int:
        assert total_elements > 0, "total_elements must be greater than zero"
        assert elements_per_page > 0, "elements_per_page must be greater than zero"

        return ((total_elements - 1) // elements_per_page) + 1


class ContextPagination(_BasePagination):
    __slots__ = ("_ctx", "_msg")

    def __init__(
        self, timeout: float, fetch_page: FetchPage, ctx: commands.Context
    ) -> None:
        super().__init__(timeout, fetch_page)

        self._ctx = ctx

    async def interaction_check(self, interaction: Interaction) -> bool:
        return self._ctx.author.id == interaction.user.id

    async def on_timeout(self):
        await self._msg.edit(view=None)

    async def _send_message(self, *, embed: Embed, view: Optional[View] = None) -> None:
        self._msg = await self._ctx.send(embed=embed, view=view)  # pyright: ignore[reportArgumentType, reportCallIssue]

    async def _edit_message(self, *, embed: Embed, view: View) -> None:
        await self._msg.edit(embed=embed, view=view)


class InteractionPagination(_BasePagination):
    __slots__ = ("_interaction",)

    def __init__(
        self, timeout: float, fetch_page: FetchPage, interaction: Interaction
    ) -> None:
        super().__init__(timeout, fetch_page)

        self._interaction = interaction

    async def interaction_check(self, interaction: Interaction) -> bool:
        return self._interaction.user.id == interaction.user.id

    async def on_timeout(self):
        original_response = await self._interaction.original_response()
        await original_response.edit(view=None)

    async def _send_message(self, *, embed: Embed, view: Optional[View] = None) -> None:
        await self._interaction.response.send_message(
            embed=embed,
            view=view,  # pyright: ignore[reportArgumentType]
            ephemeral=True,
        )

    async def _edit_message(self, *, embed: Embed, view: View) -> None:
        await self._interaction.response.edit_message(embed=embed, view=view)
