__all__ = ["Waiter", "Event"]


import asyncio


class _Signal:
    __slots__ = (
        "_active",
        "_fut",
    )

    def __init__(self) -> None:
        self._active = False
        self._fut = asyncio.Future()

    def set(self) -> None:
        if self._active:
            return
        self._active = True

        if not self._fut.done():
            self._fut.set_result(None)
        self._fut = asyncio.Future()

    async def wait(self) -> None:
        await self._fut

        self._active = False


class Waiter:
    __slots__ = ("_event", "_signal", "_done")

    def __init__(self, event: "Event") -> None:
        self._event = event
        self._signal = _Signal()
        self._done = False

    def _notify(self) -> None:
        self._signal.set()

    async def wait(self) -> bool:
        if self._done:
            return False

        await self._signal.wait()

        return True

    def done(self) -> None:
        if self._done:
            return
        self._done = True

        self._notify()

        self._event._delete(self)


class Event:
    __slots__ = ("_waiters",)

    def __init__(self) -> None:
        self._waiters: set[Waiter] = set()

    def waiter(self) -> Waiter:
        waiter = Waiter(self)

        self._waiters.add(waiter)

        return waiter

    def _delete(self, waiter: Waiter) -> None:
        self._waiters.discard(waiter)

    def notify(self) -> None:
        for waiter in self._waiters:
            waiter._notify()
