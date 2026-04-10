__all__ = ["CooldownPreset"]

from typing import Optional


_DEFAULT_COOLDOWN_RATE = 2
_DEFAULT_COOLDOWN_TIME = 2.0


class CooldownPreset:
    __slots__ = ("_rate", "_time")

    def __init__(self, rate: Optional[int] = None, time: Optional[int] = None) -> None:
        self._rate = rate if rate else _DEFAULT_COOLDOWN_RATE
        self._time = time if time else _DEFAULT_COOLDOWN_TIME

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def time(self) -> float:
        return self._time
