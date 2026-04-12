from abc import ABC
from dataclasses import dataclass, fields
from configparser import ConfigParser, SectionProxy
from pathlib import Path
from typing import Optional, get_args

__all__ = [
    "ConfigError",
    "MissingField",
    "InvalidField",
    "Bot",
    "Lavalink",
    "Database",
    "Config",
    "parse",
]


class ConfigError(Exception):
    pass


class MissingSection(ConfigError):
    def __init__(self, section: str) -> None:
        super().__init__(f"missing config section {section}")


class MissingField(ConfigError):
    def __init__(self, section: str, field: str) -> None:
        super().__init__(f"missing field {field} in section {section}")


class InvalidField(ConfigError):
    def __init__(self, section: str, field: str) -> None:
        super().__init__(f"field {field} has invalid type in section {section}")


@dataclass
class _Section(ABC):
    pass


@dataclass
class _OptionalSection(ABC):
    pass


@dataclass
class Bot(_Section):
    token: str
    description: Optional[str] = None
    activity: Optional[str] = None


@dataclass
class Player(_OptionalSection):
    queue_size: Optional[int] = None


@dataclass
class Lavalink(_Section):
    name: str
    host: str
    port: int
    password: str
    region: str


@dataclass
class Cache(_OptionalSection):
    entries: Optional[int] = None
    ttl: Optional[int] = None


@dataclass
class Database(_Section):
    uri: str


@dataclass
class Commands(_OptionalSection):
    cooldown_rate: Optional[int] = None
    cooldown_time: Optional[int] = None


@dataclass
class Config:
    bot: Bot
    player: Player
    lavalink: Lavalink
    database: Database
    cache: Cache
    commands: Commands


def _read(path: Path) -> ConfigParser:
    raw = ConfigParser()

    with open(path, "r") as f:
        raw.read_file(f)

    return raw


def _extract_section(section_proxy: SectionProxy, section: type[_Section]) -> _Section:
    kwargs = {}

    for field in fields(section):
        if field.name not in section_proxy:
            if field.default is None:
                continue
            raise MissingField(section_proxy.name, field.name)
        try:
            ftype = types[0] if (types := get_args(field.type)) else field.type
            kwargs[field.name] = ftype(section_proxy[field.name])  # pyright: ignore reportCallIssue
        except ValueError:
            raise InvalidField(section_proxy.name, field.name)

    return section(**kwargs)


def _extract_config(config_parser: ConfigParser) -> Config:
    kwargs = {}

    for field in fields(Config):
        section: type[_Section] = field.type  # pyright: ignore reportAssignmentType
        if field.name not in config_parser:
            if issubclass(section, _OptionalSection):
                kwargs[field.name] = section()
                continue
            raise MissingSection(field.name)
        section_proxy = config_parser[field.name]
        kwargs[field.name] = _extract_section(section_proxy, section)

    return Config(**kwargs)


def parse(path: Path) -> Config:
    config_parser = _read(path)

    return _extract_config(config_parser)
