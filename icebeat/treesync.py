import asyncio
from inspect import getmembers, ismethod
import inspect
import logging
from typing import Callable, Coroutine, Optional

from attr import dataclass
from discord.abc import Snowflake
from discord.app_commands import AppCommand
from discord.ext.commands import Cog


__all__ = [
    "AppCommands",
    "RegisteredAppCommands",
    "RemovedAppCommands",
    "tree_sync_listener",
    "TreeSyncEvents",
]


_TREE_SYNC_LISTENER_EVENT_TAG = "_tree_sync_listener_event"


__log__ = logging.getLogger(__name__)


class TreeSyncError(Exception):
    pass


class InvalidEventHook(TreeSyncError):
    def __init__(self, name: str) -> None:
        super().__init__(f"event hook {name} must return a coroutine")


class AppCommands:
    __slots__ = ("_commands",)

    def __init__(self, commands: list[AppCommand]) -> None:
        self._commands = {command.name: command for command in commands}

    def get(self, name: str) -> Optional[AppCommand]:
        return self._commands.get(name)


@dataclass
class TreeSyncEvent:
    guild: Snowflake


@dataclass
class RegisteredAppCommands(TreeSyncEvent):
    commands: AppCommands


@dataclass
class RemovedAppCommands(TreeSyncEvent):
    pass


AppCommandsEventHook = Callable[..., Coroutine[None, None, None]]


def tree_sync_listener(
    event: type[TreeSyncEvent],
):
    def decorator(listener: AppCommandsEventHook) -> AppCommandsEventHook:
        setattr(listener, _TREE_SYNC_LISTENER_EVENT_TAG, event)

        return listener

    return decorator


class TreeSyncEvents:
    __slots__ = ("_event_hooks",)

    def __init__(self) -> None:
        self._event_hooks: dict[str, list[AppCommandsEventHook]] = {}

    def dispatch(
        self,
        event: TreeSyncEvent,
    ) -> None:
        async def wrapper(event_hook: AppCommandsEventHook) -> None:
            try:
                await event_hook(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                __log__.warning(
                    "Failed to execute cached app commands event hook %s %s",
                    event_hook.__name__,
                    e,
                )

        event_name = event.__class__.__name__
        if event_hooks := self._event_hooks.get(event_name):
            for event_hook in event_hooks:
                asyncio.create_task(wrapper(event_hook))

    def register_hooks(self, cog: Cog) -> None:
        event_hooks = getmembers(
            cog,
            predicate=lambda method: (
                ismethod(method) and hasattr(method, _TREE_SYNC_LISTENER_EVENT_TAG)
            ),
        )
        for hook_name, event_hook in event_hooks:
            if not inspect.iscoroutinefunction(event_hook):
                raise InvalidEventHook(hook_name)
            event_name = getattr(event_hook, _TREE_SYNC_LISTENER_EVENT_TAG).__name__
            if event_hooks := self._event_hooks.get(event_name):
                event_hooks.append(event_hook)
            else:
                self._event_hooks[event_name] = [event_hook]
