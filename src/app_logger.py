"""
診断用ログ。コンソールとファイルの両方へ出力し、即 flush する。
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

_LOGGER_NAME = "esp2dsd"
_configured = False
_log_path: Path | None = None
_lock = threading.Lock()


def setup_logging(base_path: Path, log_filename: str = "esp2dsd.log") -> Path:
    """
    ログ初期化。exe / 実行ベース直下に esp2dsd.log を書く。
    """
    global _configured, _log_path

    with _lock:
        log_path = base_path.joinpath(log_filename)
        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.propagate = False

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        _configured = True
        _log_path = log_path
        logger.info("Log file: %s", log_path.resolve())
        return log_path


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        # setup 前でも落ちないよう stderr へ最低限出す
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(
                logging.Formatter("[%(levelname)s] %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
    return logger


def get_log_path() -> Path | None:
    return _log_path
