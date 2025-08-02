from dataclasses import dataclass
from enum import Enum
from typing import Optional

__all__ = ["Filter", "Guild"]


class Filter(Enum):
    NORMAL = 0
    BASS_BOOST = 1
    POP = 2
    SOFT = 3
    TREBLLEBASS = 4
    EIGHTD = 5
    KARAOKE = 6


@dataclass
class Guild:
    id: int
    text_channel_id: Optional[int]
    filter: Filter
    volume: float
    auto_leave: bool


@dataclass
class Whitelist:
    guild_ids: set[int]
