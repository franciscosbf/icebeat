import logging

import colorlog

__all__ = ["setup_logger"]


_PKG_NAME = __name__.split(".")[0]


class _LogFilter(logging.Filter):
    __slots__ = ("_verbose",)

    def __init__(self, verbose: bool) -> None:
        super().__init__()

        self._verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        return self._verbose or record.name.startswith(_PKG_NAME)


def setup_logger(verbose: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO

    handler = colorlog.StreamHandler()
    handler.addFilter(_LogFilter(verbose))
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "[%(asctime)s] [%(name)s] [%(log_color)s%(levelname)s%(reset)s] %(message)s"
        )
    )

    logging.basicConfig(
        level=level,
        handlers=(handler,),
    )
