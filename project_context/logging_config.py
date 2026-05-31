import logging
import os
from pathlib import Path
from typing import cast


def logger_formatter(for_file: bool = False) -> logging.Formatter:
    if for_file:
        return logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        return logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%H:%M:%S",
        )


def handler_stream(formatter: logging.Formatter, level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    return console_handler


def handler_file(path: str, formatter: logging.Formatter) -> logging.FileHandler:
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    return file_handler


def setup_logging(log_file: Path, is_debug: bool, max_history: int = 20) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    existing_logs = cast(list[Path],sorted(log_file.parent.glob("*.log"), key=os.path.getmtime))
    if len(existing_logs) > max_history:
        for old_log in existing_logs[:-max_history]:
            try:
                old_log.unlink()
            except Exception:
                pass

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        handler.close()

    handlers = []

    # Handler para archivo Siempre DEBUG
    file_h = logging.FileHandler(log_file, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logger_formatter(True))
    handlers.append(file_h)

    if is_debug:
        # Handler para consola INFO por defecto
        console_h = logging.StreamHandler()
        console_h.setLevel(logging.INFO)
        console_h.setFormatter(logger_formatter(False))
        handlers.append(console_h)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=handlers,
    )

    for lib in ["pyrogram", "peewee", "urllib3"]:
        logging.getLogger(lib).setLevel(logging.INFO)
