import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RedactingFormatter(logging.Formatter):
    def __init__(self, token: str):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.token = token

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return text.replace(self.token, "[REDACTED]") if self.token else text


def configure_logging(level: str, token: str, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler()
    file = RotatingFileHandler(
        directory / "wabot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    formatter = RedactingFormatter(token)
    for handler in (console, file):
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[console, file], force=True)
    # Library debug output can contain interaction payloads and SQL parameters.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
