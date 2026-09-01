"""Testa o supervisor de reconexão do entrypoint.

Não precisa de token, de rede nem de banco: substitui o Zenibot por um duplo
que falha conforme um roteiro. Rode depois de mexer em __main__.py:

    python scripts/test_reconnect.py

Cobre o cenário real que derrubava o bot — DNS indisponível na partida — e a
regra oposta, igualmente importante: erros de configuração (token inválido,
intent faltando) NÃO podem virar laço de retentativa.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["ZENIBOT_TOKEN"] = "x" * 60
os.environ["ZENIBOT_OWNER_IDS"] = "1"
os.environ["ZENIBOT_LOG_LEVEL"] = "CRITICAL"  # silencia o ruído esperado

import discord  # noqa: E402

from zenibot import __main__ as entry  # noqa: E402
from zenibot.config import load_settings  # noqa: E402

falhas: list[str] = []


def check(nome: str, ok: bool, detalhe: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FALHA'} {nome}{(' — ' + detalhe) if detalhe else ''}")
    if not ok:
        falhas.append(nome)


class FakeBot:
    """Duplo do Zenibot: conta tentativas e falha conforme o roteiro."""

    tentativas = 0
    roteiro: list[BaseException | None] = []
    fechado = 0
    user = None  # como o Zenibot antes do login

    def __init__(self, settings) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        FakeBot.fechado += 1

    async def close(self) -> None:
        FakeBot.fechado += 1

    async def start(self, token: str) -> None:
        i = FakeBot.tentativas
        FakeBot.tentativas += 1
        resultado = FakeBot.roteiro[i] if i < len(FakeBot.roteiro) else None
        if resultado is not None:
            raise resultado


def preparar(roteiro: list[BaseException | None]) -> None:
    FakeBot.tentativas = 0
    FakeBot.fechado = 0
    FakeBot.roteiro = roteiro
    entry.Zenibot = FakeBot  # type: ignore[misc]
    # Backoff mínimo para o teste rodar rápido.
    entry.INITIAL_BACKOFF = 0.01
    entry.MAX_BACKOFF = 0.02
    entry.STABLE_SESSION = 9999  # nenhuma sessão do teste conta como estável


async def main() -> None:
    settings = load_settings()

    print("\n[1] Falha de DNS na partida — o cenário que derrubou o bot")
    # ClientConnectorDNSError é o que o aiohttp levanta; usamos a superclasse
    # ClientConnectionError, que é o que a tupla RETRYABLE precisa capturar.
    preparar([OSError("[Errno 11001] getaddrinfo failed"), None])
    await entry.supervise(settings)
    check(
        "reconecta apos falha de DNS",
        FakeBot.tentativas == 2,
        f"{FakeBot.tentativas} tentativas",
    )

    print("\n[2] Varias falhas seguidas")
    import aiohttp
    preparar([
        aiohttp.ClientConnectionError("conn recusada"),
        TimeoutError(),
        OSError("rede inacessivel"),
        None,
    ])
    await entry.supervise(settings)
    check("insiste ate conectar", FakeBot.tentativas == 4, f"{FakeBot.tentativas} tentativas")

    print("\n[3] Token invalido NAO deve ser repetido")
    preparar([discord.LoginFailure("401"), None])
    try:
        await entry.supervise(settings)
        check("propaga LoginFailure", False, "nao levantou")
    except discord.LoginFailure:
        check("propaga LoginFailure", True)
    check("nao tentou de novo", FakeBot.tentativas == 1, f"{FakeBot.tentativas} tentativa(s)")

    print("\n[4] Intent privilegiado faltando NAO deve ser repetido")
    preparar([discord.PrivilegedIntentsRequired(shard_id=None), None])
    try:
        await entry.supervise(settings)
        check("propaga PrivilegedIntentsRequired", False, "nao levantou")
    except discord.PrivilegedIntentsRequired:
        check("propaga PrivilegedIntentsRequired", True)
    check("nao tentou de novo", FakeBot.tentativas == 1, f"{FakeBot.tentativas} tentativa(s)")

    print("\n[5] Conexao limpa encerra sem repetir")
    preparar([None])
    await entry.supervise(settings)
    check("sai apos start() limpo", FakeBot.tentativas == 1, f"{FakeBot.tentativas} tentativa(s)")

    print("\n[6] Backoff cresce e respeita o teto")
    preparar([OSError("x")] * 6 + [None])
    entry.INITIAL_BACKOFF = 0.01
    entry.MAX_BACKOFF = 0.03
    inicio = asyncio.get_running_loop().time()
    await entry.supervise(settings)
    decorrido = asyncio.get_running_loop().time() - inicio
    # 6 esperas: 0.01,0.02,0.03,0.03,0.03,0.03 = 0.15s com teto; sem teto
    # a soma passaria de 0.6s. Jitter de +-20% mantem a folga.
    check("respeita o teto de backoff", decorrido < 0.4, f"{decorrido:.3f}s em 6 tentativas")
    check("tentou 7 vezes", FakeBot.tentativas == 7, f"{FakeBot.tentativas}")

    print("\n[7] Recursos liberados a cada tentativa")
    preparar([OSError("x"), OSError("y"), None])
    await entry.supervise(settings)
    check("fecha o bot em toda tentativa", FakeBot.fechado >= 3, f"{FakeBot.fechado} fechamentos")


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + "=" * 60)
    if falhas:
        print(f"{len(falhas)} FALHA(S): {falhas}")
        raise SystemExit(1)
    print("Todos os checks passaram.")
