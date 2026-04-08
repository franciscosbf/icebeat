from dataclasses import dataclass
from enum import Enum
from typing import Optional

__all__ = ["Filter", "Guild"]


class Filter(Enum):
    normal = 1 << 0
    bassboost = 1 << 1
    pop = 1 << 2
    soft = 1 << 3
    treblebass = 1 << 4
    eightd = 1 << 5
    karaoke = 1 << 6
    vaporwave = 1 << 7


@dataclass
class Guild:
    id: int
    staff_role_id: Optional[int]
    filter: Filter
    volume: int
    auto_leave: bool
    shuffle: bool
    loop: bool


@dataclass
class Whitelist:
    guild_ids: set[int]
