from dataclasses import dataclass
from enum import Enum
from typing import Optional

__all__ = ["Filter", "Guild"]


class Filter(Enum):
    normal = 0
    bassboost = 1
    pop = 2
    soft = 3
    treblebass = 4
    eightd = 5
    karaoke = 6
    vaporwave = 7


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
