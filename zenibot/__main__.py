"""Entrypoint: python -m zenibot

Supervisiona o bot: falhas de rede viram nova tentativa com backoff
exponencial, em vez de derrubar o processo.

Divisão de responsabilidades com o discord.py: uma vez conectado, o
`Client.connect(reconnect=True)` já lida com quedas do Gateway, RESUME e
reconexão. O que ele **não** cobre é a falha no `login()` inicial — a
requisição HTTP a /users/@me antes do WebSocket existir. Sem rede na partida,
a exceção sobe e mata o processo. É essa lacuna que o laço abaixo fecha.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
import sys
import time

import aiohttp
import discord

from zenibot.bot import Zenibot
from zenibot.config import Settings, load_settings
from zenibot.core.logging_setup import setup_logging

log = logging.getLogger("zenibot")

# Erros de configuração: tentar de novo nunca vai ajudar.
FATAL = (
    discord.LoginFailure,
    discord.PrivilegedIntentsRequired,
)

# Falhas transitórias de rede ou do lado do Discord.
# socket.gaierror (DNS) é subclasse de OSError; ClientConnectorDNSError e
# afins descendem de aiohttp.ClientError.
RETRYABLE = (
    aiohttp.ClientError,
    OSError,
    TimeoutError,  # asyncio.TimeoutError é alias disto desde o 3.11
    discord.GatewayNotFound,
    discord.DiscordServerError,
    discord.ConnectionClosed,
)

INITIAL_BACKOFF = 5.0
MAX_BACKOFF = 300.0
# Uma sessão que durou mais que isto é considerada saudável: o backoff
# volta ao início, para que uma queda amanhã não herde a espera de hoje.
STABLE_SESSION = 60.0


async def supervise(settings: Settings) -> None:
    stopping = asyncio.Event()
    current: Zenibot | None = None

    def request_shutdown() -> None:
        if stopping.is_set():
            return
        stopping.set()
        log.info("Sinal de encerramento recebido.")
        if current is not None:
            asyncio.create_task(current.close())

    # No Windows add_signal_handler não existe; lá o Ctrl+C chega como
    # KeyboardInterrupt e é tratado em __main__.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except (NotImplementedError, AttributeError):
            pass

    backoff = INITIAL_BACKOFF
    tentativa = 0

    while not stopping.is_set():
        bot = Zenibot(settings)
        current = bot
        inicio = time.monotonic()

        try:
            async with bot:
                await bot.start(settings.token)
        except FATAL:
            raise
        except RETRYABLE as exc:
            duracao = time.monotonic() - inicio
            if duracao >= STABLE_SESSION:
                backoff = INITIAL_BACKOFF
                tentativa = 0
            if stopping.is_set():
                break

            tentativa += 1
            # Jitter evita que várias instâncias reconectem em uníssono.
            espera = min(backoff, MAX_BACKOFF) * random.uniform(0.8, 1.2)
            # bot.user só é preenchido depois do login: distingue "caiu depois
            # de conectar" de "nunca chegou a conectar".
            situacao = (
                f"Conexão perdida após {duracao:.0f}s"
                if bot.user is not None
                else "Falha ao conectar"
            )
            log.warning(
                "%s (%s: %s). Tentativa %d em %.0fs.",
                situacao,
                type(exc).__name__,
                exc,
                tentativa,
                espera,
            )
            backoff = min(backoff * 2, MAX_BACKOFF)

            try:
                await asyncio.wait_for(stopping.wait(), timeout=espera)
                break  # encerramento pedido durante a espera
            except TimeoutError:
                continue
        else:
            # start() retornou sem exceção: fechamento limpo.
            break
        finally:
            current = None

    log.info("Encerrado.")


async def main() -> None:
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 — queremos uma mensagem amigável aqui
        print(f"Configuração inválida: {exc}\n", file=sys.stderr)
        print("Copie .env.example para .env e preencha ZENIBOT_TOKEN.", file=sys.stderr)
        raise SystemExit(1) from None

    setup_logging(settings.log_level)
    await supervise(settings)


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
