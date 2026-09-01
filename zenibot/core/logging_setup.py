"""Configuração de logging.

O filtro de token é obrigatório, não cosmético: um traceback de biblioteca HTTP
pode incluir o header Authorization, e um log vazado é um token vazado.
"""

from __future__ import annotations

import logging
import re
import sys


class RedactSecrets(logging.Filter):
    """Substitui tokens do Discord por [REDACTED] em qualquer registro."""

    # Formato do token: <id-base64>.<timestamp>.<hmac>
    PATTERN = re.compile(r"[\w-]{20,}\.[\w-]{6}\.[\w-]{27,}")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.PATTERN.sub("[REDACTED]", record.msg)
        if record.args:
            record.args = tuple(
                self.PATTERN.sub("[REDACTED]", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)-24s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(RedactSecrets())

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(handler)

    # discord.py é verboso demais em DEBUG; o gateway raramente interessa.
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
