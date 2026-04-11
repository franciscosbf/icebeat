__all__ = ["Waiter", "Event"]


import asyncio


class Waiter:
    __slots__ = ("_event", "_aevent", "_done")

    def __init__(self, event: "Event") -> None:
        self._event = event
        self._aevent = asyncio.Event()
        self._done = False

    def _notify(self) -> None:
        self._aevent.set()

    async def wait(self) -> bool:
        if self._done:
            return False

        await self._aevent.wait()
        self._aevent.clear()

        return True

    def done(self) -> None:
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
