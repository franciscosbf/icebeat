from abc import ABC
from dataclasses import dataclass, fields
from configparser import ConfigParser, SectionProxy
from pathlib import Path
from typing import Optional

__all__ = ["Bot", "Lavalink", "Database", "Config", "parse"]


@dataclass
class _Section(ABC):
    pass


@dataclass
class _OptionalSection(ABC):
    pass


@dataclass
class Bot(_Section):
    token: str


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
class Config:
    bot: Bot
    lavalink: Lavalink
    database: Database
    cache: Cache


def _read(path: Path) -> ConfigParser:
    raw = ConfigParser()

    with open(path, "r") as f:
        raw.read_file(f)

    return raw


def _extract_section(section_proxy: SectionProxy, section: type[_Section]) -> _Section:
    kwargs = {}

    for field in fields(section):
        if field.default is None:
            continue
        elif field.name not in section_proxy:
            raise ValueError(
                f"missing field {field.name} in section {section_proxy.name}"
            )
        try:
            kwargs[field.name] = field.type(section_proxy[field.name])  # pyright: ignore reportCallIssue
        except ValueError:
            raise TypeError(
                f"field {field.name} has invalid type in section {section_proxy.name}"
            )

    return section(**kwargs)


def _extract_config(config_parser: ConfigParser) -> Config:
    kwargs = {}

    for field in fields(Config):
        section: type[_Section] = field.type  # pyright: ignore reportAssignmentType
        if issubclass(section, _OptionalSection):
            kwargs[field.name] = section()
            continue
        elif field.name not in config_parser:
            raise ValueError(f"missing config section {field.name}")
        section_proxy = config_parser[field.name]
        kwargs[field.name] = _extract_section(section_proxy, section)

    return Config(**kwargs)


def parse(path: Path) -> Config:
    config_parser = _read(path)

    return _extract_config(config_parser)
