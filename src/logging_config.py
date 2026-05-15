import logging
import os
import sys
from typing import Optional


class EmojiFormatter(logging.Formatter):
    LEVEL_EMOJI = {
        "DEBUG": "🐛",
        "INFO": "✨",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "CRITICAL": "🔥",
    }
    LEVEL_COLOR = {
        "DEBUG": "\x1b[36m",
        "INFO": "\x1b[32m",
        "WARNING": "\x1b[33m",
        "ERROR": "\x1b[31m",
        "CRITICAL": "\x1b[35m",
    }
    RESET = "\x1b[0m"

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        fmt = fmt or "%(asctime)s %(emoji)s %(levelname)s %(name)s: %(message)s"
        datefmt = datefmt or "%Y-%m-%d %H:%M:%S"
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        record.emoji = self.LEVEL_EMOJI.get(record.levelname, "")
        msg = super().format(record)
        # Avoid ANSI colors on Windows consoles that don't support them
        if os.name == "nt":
            return msg
        color = self.LEVEL_COLOR.get(record.levelname, "")
        return f"{color}{msg}{self.RESET}"


def redact_db_url(url: str) -> str:
    if not url:
        return ""
    try:
        if "@" in url and "://" in url:
            prefix, rest = url.split("://", 1)
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _pwd = creds.split(":", 1)
                return f"{prefix}://{user}:****@{host}"
    except Exception:
        pass
    return url


def get_logger(name: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(name)
    # Configure only once per logger
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(EmojiFormatter())
        logger.addHandler(handler)
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        try:
            logger.setLevel(level)
        except Exception:
            logger.setLevel("INFO")
        logger.propagate = False
    return logger
