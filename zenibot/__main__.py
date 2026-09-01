"""Entrypoint: python -m zenibot"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord

from zenibot.bot import Zenibot
from zenibot.config import load_settings
from zenibot.core.logging_setup import setup_logging

log = logging.getLogger("zenibot")


async def main() -> None:
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 — queremos uma mensagem amigável aqui
        print(f"Configuração inválida: {exc}\n", file=sys.stderr)
        print("Copie .env.example para .env e preencha ZENIBOT_TOKEN.", file=sys.stderr)
        raise SystemExit(1) from None

    setup_logging(settings.log_level)
    bot = Zenibot(settings)

    # Shutdown gracioso em SIGTERM (Docker/systemd). No Windows,
    # add_signal_handler não existe — o Ctrl+C do run() já cobre o caso.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
        except (NotImplementedError, AttributeError):
            pass

    async with bot:
        await bot.start(settings.token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except discord.LoginFailure:
        log.error("Token inválido. Verifique ZENIBOT_TOKEN no .env.")
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Intent privilegiado não habilitado. Acesse o Developer Portal > "
            "sua aplicação > Bot > Privileged Gateway Intents e ative "
            "SERVER MEMBERS INTENT."
        )
        raise SystemExit(1) from None
