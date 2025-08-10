from abc import ABC
from dataclasses import dataclass, fields
from configparser import ConfigParser, SectionProxy
from pathlib import Path

__all__ = ["Bot", "Lavalink", "Database", "Config", "parse"]


@dataclass
class _Section(ABC):
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
class Cache(_Section):
    entries: int
    ttl: int


@dataclass
class Database(_Section):
    uri: str


@dataclass
class Config:
    bot: Bot
    lavalink: Lavalink
    cache: Cache
    database: Database


def _read(path: Path) -> ConfigParser:
    raw = ConfigParser()

    with open(path, "r") as f:
        raw.read_file(f)

    return raw


def _extract_section(section_proxy: SectionProxy, section: type[_Section]) -> _Section:
    kwargs = {}

    for field in fields(section):
        if field.name not in section_proxy:
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
        if field.name not in config_parser:
            raise ValueError(f"missing config section {field.name}")
        section_proxy = config_parser[field.name]
        kwargs[field.name] = _extract_section(section_proxy, field.type)  # pyright: ignore reportArgumentType

    return Config(**kwargs)


def parse(path: Path) -> Config:
    config_parser = _read(path)

    return _extract_config(config_parser)
