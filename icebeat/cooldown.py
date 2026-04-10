from typing import Optional

__all__ = [
    "CooldownPresetError",
    "InvalidRateError",
    "InvalidTimeError",
    "CooldownPreset",
]


_DEFAULT_COOLDOWN_RATE = 2
_DEFAULT_COOLDOWN_TIME = 2.0


class CooldownPresetError(Exception):
    pass


class InvalidRateError(CooldownPresetError):
    def __init__(self) -> None:
        super().__init__("rate must be greater than zero")


class InvalidTimeError(CooldownPresetError):
    def __init__(self) -> None:
        super().__init__("time must be greater than zero")


class CooldownPreset:
    __slots__ = ("_rate", "_time")

    def __init__(self, rate: Optional[int] = None, time: Optional[int] = None) -> None:
        self._rate = rate if rate else _DEFAULT_COOLDOWN_RATE
        self._time = time if time else _DEFAULT_COOLDOWN_TIME

        if self._rate < 1:
            raise InvalidRateError()

        if self._time < 1:
            raise InvalidTimeError()

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def time(self) -> float:
        return self._time
