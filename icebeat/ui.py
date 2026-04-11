from abc import ABC, abstractmethod
import asyncio
import logging
from typing import Optional, override
from discord import Button, ButtonStyle, Embed, HTTPException, Interaction
from discord.ext import commands
from discord.ui import Item, View, button


__all__ = ["compute_total_pages", "Page", "ContextPagination", "InteractionPagination"]

__log__ = logging.getLogger(__name__)


def compute_total_pages(total_elements: int, elements_per_page: int) -> int:
    return ((total_elements - 1) // elements_per_page) + 1


class Page(ABC):
    @abstractmethod
    async def fetch(self, current_page: int) -> tuple[Embed, int, int]: ...

    @abstractmethod
    def unavailable_page_alert(self) -> Embed: ...

    @abstractmethod
    async def wait_for_edit_request(self) -> None: ...

    @abstractmethod
    def cancel_edit_request(self) -> None: ...


class _BasePagination(ABC, View):
    __slots__ = (
        "_navigated",
        "_page",
        "_current_page",
        "_total_pages",
        "_edit_page_lock",
        "_dynamic_edit_page_task",
    )

    def __init__(self, timeout: float, page: Page) -> None:
        super().__init__(timeout=timeout)

        self._navigated = False
        self._page = page
        self._current_page = 1
        self._total_pages = 1
        self._edit_page_lock = asyncio.Lock()
        self._dynamic_edit_page_task: asyncio.Task

    @abstractmethod
    async def _send_message(
        self,
        *,
        embed: Embed,
        view: View,
    ) -> None: ...

    @abstractmethod
    async def _edit_message(
        self, *, embed: Embed, view: Optional[View] = None
    ) -> None: ...

    def _update_buttons(self):
        self.children[0].disabled = self._current_page == 1  # pyright: ignore[reportAttributeAccessIssue]
        self.children[1].disabled = self._current_page == self._total_pages  # pyright: ignore[reportAttributeAccessIssue]

    async def _update_view(self) -> Embed:
        embed, self._current_page, self._total_pages = await self._page.fetch(
            self._current_page
        )

        self._update_buttons()

        return embed

    async def _edit_page(self, interaction: Interaction):
        async with self._edit_page_lock:
            embed = await self._update_view()
            await interaction.response.edit_message(embed=embed, view=self)

    def _dispatch_dynamic_edit_page(self) -> None:
        async def dynamic_edit_page():
            try:
                while True:
                    await self._page.wait_for_edit_request()

                    async with self._edit_page_lock:
                        embed = await self._update_view()
                        await self._edit_message(embed=embed, view=self)
            except asyncio.CancelledError:
                self._page.cancel_edit_request()
            except Exception as e:
                __log__.warning("Dynamic page edition raised an error: %s", e)

        self._dynamic_edit_page_task = asyncio.create_task(dynamic_edit_page())

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

    @override
    async def on_error(
        self, interaction: Interaction, error: Exception, item: Item, /
    ) -> None:
        _ = item

        self._dynamic_edit_page_task.cancel()

        __log__.warning(f"Paginated view has failed: {error}")

        if not isinstance(error, HTTPException):
            embed = self._page.unavailable_page_alert()
            await interaction.response.edit_message(embed=embed, view=None)

    @override
    async def on_timeout(self) -> None:
        self._dynamic_edit_page_task.cancel()

        embed = self._page.unavailable_page_alert()
        await self._edit_message(embed=embed)

    async def navigate(self):
        if self._navigated:
            return
        self._navigated = True

        embed = await self._update_view()
        await self._send_message(embed=embed, view=self)

        self._dispatch_dynamic_edit_page()


class ContextPagination(_BasePagination):
    __slots__ = ("_ctx", "_msg")

    def __init__(
        self,
        timeout: float,
        page: Page,
        ctx: commands.Context,
    ) -> None:
        super().__init__(timeout, page)

        self._ctx = ctx

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        return self._ctx.author.id == interaction.user.id

    async def _send_message(
        self,
        *,
        embed: Embed,
        view: View,
    ) -> None:
        self._msg = await self._ctx.reply(embed=embed, view=view)

    async def _edit_message(self, *, embed: Embed, view: Optional[View] = None) -> None:
        await self._msg.edit(embed=embed, view=view)


class InteractionPagination(_BasePagination):
    __slots__ = ("_interaction",)

    def __init__(
        self,
        timeout: float,
        page: Page,
        interaction: Interaction,
    ) -> None:
        super().__init__(timeout, page)

        self._interaction = interaction

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        return self._interaction.user.id == interaction.user.id

    async def _send_message(
        self,
        *,
        embed: Embed,
        view: View,
    ) -> None:
        await self._interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )

    async def _edit_message(self, *, embed: Embed, view: Optional[View] = None) -> None:
        response = await self._interaction.original_response()
        await response.edit(embed=embed, view=view)
