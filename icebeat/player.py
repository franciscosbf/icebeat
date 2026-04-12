from typing import Optional, SupportsIndex
from discord.utils import classproperty
from typing_extensions import override
import lavalink

from icebeat.notify import Event, Waiter

__all__ = [
    "IceBeatPlayerError",
    "InvalidQueueSize",
    "QueueIsFull",
    "Queue",
    "IceBeatPlayer",
]

_DEFAULT_MAX_QUEUE_SIZE = 1000


class IceBeatPlayerError(Exception):
    pass


class InvalidQueueSize(IceBeatPlayerError):
    def __init__(self) -> None:
        super().__init__("player queue size must be greater than zero")


class QueueIsFull(IceBeatPlayerError):
    def __init__(self) -> None:
        super().__init__("queue is full")


class Queue(list[lavalink.AudioTrack]):
    _MAX_SIZE: int = _DEFAULT_MAX_QUEUE_SIZE

    __slots__ = ("_notifier", "_free_slots")

    def __init__(self) -> None:
        super().__init__()

        self._notifier = Event()
        self._free_slots = self._MAX_SIZE

    @classmethod
    def set_max_size(cls, max_size: int) -> None:
        if max_size < 1:
            raise InvalidQueueSize()

        cls._MAX_SIZE = max_size

    @classproperty
    def max_size(cls) -> int:
        return cls._MAX_SIZE

    def _update_free_slots(self) -> None:
        self._free_slots = self._MAX_SIZE - len(self)

    def _notify(self) -> None:
        self._notifier.notify()

    @property
    def free_slots(self) -> int:
        return self._free_slots

    def is_full(self) -> bool:
        return self._free_slots == 0

    def waiter(self) -> Waiter:
        return self._notifier.waiter()

    @override
    def append(self, track: lavalink.AudioTrack, /) -> None:
        if self.is_full():
            raise QueueIsFull()

        super().append(track)

        self._update_free_slots()
        self._notify()

    @override
    def pop(self, index: SupportsIndex = -1, /) -> lavalink.AudioTrack:
        track = super().pop(index)

        self._update_free_slots()
        self._notify()

        return track

    @override
    def insert(self, index: SupportsIndex, track: lavalink.AudioTrack, /) -> None:
        if self.is_full():
            raise QueueIsFull()

        super().insert(index, track)

        self._update_free_slots()
        self._notify()

    @override
    def clear(self) -> None:
        super().clear()

        self._update_free_slots()
        self._notify()

    def shrink(self, start: int) -> None:
        self[:] = self[start:]

        self._update_free_slots()
        self._notify()


class IceBeatPlayer(lavalink.DefaultPlayer):
    __slots__ = (
        "_current",
        "_current_notifier",
    )

    def __init__(self, guild_id: int, node: lavalink.Node) -> None:
        # Hacky solution to hijack queue to notify on changes.
        self._current: Optional[lavalink.AudioTrack] = None
        self._current_notifier = Event()

        super().__init__(guild_id, node)

        # Hacky solution to hijack current track to notify on changes.
        self.queue: Queue = Queue()  # pyright: ignore[reportIncompatibleVariableOverride]

    @override
    def cleanup(self):
        self.current = None
        self.queue.clear()

    def current_waiter(self) -> Waiter:
        return self._current_notifier.waiter()

    @property
    def current(self) -> Optional[lavalink.AudioTrack]:
        return self._current

    @current.setter
    def current(self, track: Optional[lavalink.AudioTrack]) -> None:
        self._current = track

        self._current_notifier.notify()
