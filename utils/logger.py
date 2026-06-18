"""Настройка стандартного логирования в stdout.

setup_logging() — конфигурирует корневой логгер (вызывать один раз при старте).
get_logger(name) — возвращает именованный логгер для модуля.
"""

import logging
import sys

# Формат: время, уровень, имя модуля, сообщение
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Флаг, чтобы не настраивать логирование повторно
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Настраивает вывод логов в stdout с единым форматом."""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Возвращает логгер с указанным именем (обычно __name__)."""
    return logging.getLogger(name)


def log_exception(
    logger: logging.Logger,
    message: str,
    *,
    level: int = logging.ERROR,
) -> None:
    """Логирует сообщение вместе с полным traceback текущего исключения.

    Вызывать строго внутри блока ``except`` — traceback берётся из контекста
    активного исключения (``exc_info=True``). Уровень настраивается (ERROR по
    умолчанию, для критических — logging.CRITICAL).
    """
    logger.log(level, message, exc_info=True)
