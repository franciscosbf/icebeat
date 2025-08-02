from types import TracebackType
from typing import Optional, Type

import lavalink

__all__ = ["LavalinkClient"]


class LavalinkClient:
    __slots__ = ("_client",)

    def __init__(self, user_id: int) -> None:
        self._client = lavalink.Client(user_id)

    async def __aenter__(self) -> lavalink.Client:
        return self._client

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        _, _, _ = exc_type, exc_value, traceback

        await self._client.close()
