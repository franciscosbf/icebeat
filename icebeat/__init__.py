import argparse
import asyncio
import aiosqlite


from .logger import setup_logger
from .cache import TimedCache
from .storage import SQLiteStorage
from .store import Store
from . import config
from .bot import IceBeat

__all__ = ["main"]


async def _launch(conf: config.Config) -> None:
    async with aiosqlite.connect(conf.database.uri) as sqlite_conn:
        cache = TimedCache(conf.cache.entries, conf.cache.ttl)
        storage = SQLiteStorage(sqlite_conn)
        store = Store(cache, storage)

        await store.prepare()

        async with IceBeat(store, conf) as bot:
            await bot.start(conf.bot.token)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IceBeat, a Discord music bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-c", "--config", default="config.ini", help="config file path")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="output logs of internal components",
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="output debugging logs"
    )
    args = parser.parse_args()

    try:
        conf = config.parse(args.config)
    except Exception as e:
        raise SystemExit(f"Failed to parse config: {e}")

    setup_logger(args.verbose, args.debug)

    try:
        asyncio.run(_launch(conf))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        raise SystemExit(f"Failed to run bot: {e}")
