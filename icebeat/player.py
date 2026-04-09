from typing import Optional, Union
from typing_extensions import override
import lavalink
from lavalink.common import MISSING

__all__ = ["IceBeatPlayerError", "InvalidQueueSize", "QueueIsFull", "IceBeatPlayer"]

_DEFAULT_MAX_QUEUE_SIZE = 1000


class IceBeatPlayerError(Exception):
    pass


class InvalidQueueSize(IceBeatPlayerError):
    def __init__(self) -> None:
        super().__init__("queue size must be greater than zero")


class QueueIsFull(IceBeatPlayerError):
    def __init__(self) -> None:
        super().__init__("queue is full")


class IceBeatPlayer(lavalink.DefaultPlayer):
    _MAX_QUEUE_SIZE: int = _DEFAULT_MAX_QUEUE_SIZE

    __slots__ = ("_free_queue_slots",)

    def __init__(self, guild_id: int, node: lavalink.Node) -> None:
        super().__init__(guild_id, node)

        self._update_free_queue_slots()

    @classmethod
    def set_queue_size(cls, queue_size: int) -> None:
        if queue_size < 1:
            raise InvalidQueueSize()

        cls._MAX_QUEUE_SIZE = queue_size

    @property
    def free_queue_slots(self) -> int:
        return self._free_queue_slots

    @property
    def max_queue_size(self) -> int:
        return self._MAX_QUEUE_SIZE

    def _update_free_queue_slots(self) -> None:
        self._free_queue_slots = self._MAX_QUEUE_SIZE - len(self.queue)

    def is_queue_full(self) -> bool:
        return self._free_queue_slots == 0

    @override
    def add(
        self,
        track: Union[
            lavalink.AudioTrack,
            lavalink.DeferredAudioTrack,
            dict[str, Union[Optional[str], bool, int]],
        ],
        requester: int = 0,
        index: Optional[int] = None,
    ) -> None:
        if self.is_queue_full():
            raise QueueIsFull()

        super().add(track, requester, index)

        self._update_free_queue_slots()

    @override
    async def play(
        self,
        track: Optional[
            Union[
                lavalink.AudioTrack,
                lavalink.DeferredAudioTrack,
                dict[str, Union[Optional[str], bool, int]],
            ]
        ] = None,
        start_time: int = MISSING,
        end_time: int = MISSING,
        no_replace: bool = MISSING,
        volume: int = MISSING,
        pause: bool = MISSING,
        **kwargs,
    ) -> None:
        await super().play(
            track, start_time, end_time, no_replace, volume, pause, **kwargs
        )

        self._update_free_queue_slots()
