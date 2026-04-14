from abc import ABC, abstractmethod
import asyncio
import logging
from typing import Optional, override
from discord import (
    Button,
    ButtonStyle,
    Embed,
    HTTPException,
    Interaction,
)
from discord.ext import commands
from discord.ui import Item, View, button
from discord.utils import MISSING


__all__ = ["compute_total_pages", "Page", "ContextPagination", "InteractionPagination"]

__log__ = logging.getLogger(__name__)


def compute_total_pages(total_elements: int, elements_per_page: int) -> int:
    return ((total_elements - 1) // elements_per_page) + 1


class Page(ABC):
    @abstractmethod
    async def fetch(self, current_page: int) -> tuple[Embed, int, int, bool]: ...

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
    async def _edit_message(self, *, embed: Embed, view: Optional[View]) -> None: ...

    def _update_buttons(self) -> None:
        self.children[0].disabled = self._current_page == 1  # pyright: ignore[reportAttributeAccessIssue]
        self.children[1].disabled = self._current_page == self._total_pages  # pyright: ignore[reportAttributeAccessIssue]

    async def _update_page(self) -> tuple[Embed, bool]:
        embed, self._current_page, self._total_pages, empty = await self._page.fetch(
            self._current_page
        )
        assert self._current_page > 0
        assert self._total_pages > 0
        assert self._current_page <= self._total_pages

        self._update_buttons()

        return embed, empty

    async def _edit_page(self, interaction: Interaction):
        async with self._edit_page_lock:
            embed, empty = await self._update_page()
            await interaction.response.edit_message(
                embed=embed, view=None if empty else self
            )

    def _cancel_edit_page_task(self) -> None:
        if self._dynamic_edit_page_task.cancelled():
            return

        self._dynamic_edit_page_task.cancel()

    def _dispatch_dynamic_edit_page(self) -> None:
        async def dynamic_edit_page():
            try:
                while True:
                    await self._page.wait_for_edit_request()

                    async with self._edit_page_lock:
                        embed, empty = await self._update_page()
                        await self._edit_message(
                            embed=embed, view=None if empty else self
                        )
            except HTTPException:
                self.stop()
            except Exception:
                pass
            finally:
                self._page.cancel_edit_request()

        self._dynamic_edit_page_task = asyncio.create_task(dynamic_edit_page())

    async def _try_send_unavailable_page_alert(self) -> None:
        embed = self._page.unavailable_page_alert()
        try:
            await self._edit_message(embed=embed, view=None)
        except HTTPException:
            pass

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
        _, _ = interaction, item

        self._cancel_edit_page_task()

        self.stop()

        if isinstance(error, HTTPException):
            return

        __log__.warning(f"Paginated view has failed: {error}")

        await self._try_send_unavailable_page_alert()

    @override
    async def on_timeout(self) -> None:
        self._cancel_edit_page_task()

        await self._try_send_unavailable_page_alert()

    async def navigate(self):
        if self._navigated:
            return
        self._navigated = True

        embed, empty = await self._update_page()
        await self._send_message(embed=embed, view=MISSING if empty else self)

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
        self._msg = await self._ctx.reply(
            embed=embed,
            view=view,
        )

    async def _edit_message(self, *, embed: Embed, view: Optional[View]) -> None:
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
            embed=embed,
            view=view,
            ephemeral=True,
        )

    async def _edit_message(self, *, embed: Embed, view: Optional[View]) -> None:
        await self._interaction.edit_original_response(embed=embed, view=view)
