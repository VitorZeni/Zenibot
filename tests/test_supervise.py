"""Supervisor de reconexão do entrypoint.

Substitui o Zenibot por um duplo que falha conforme um roteiro, então nenhum
destes testes toca a rede.
"""

from __future__ import annotations

import asyncio

import aiohttp
import discord
import pytest

from zenibot import __main__ as entry


class FakeBot:
    """Duplo do Zenibot: conta tentativas e falha sob demanda."""

    def __init__(self, settings, *, background_tasks: bool = True) -> None:
        self.user = None  # como o bot real antes do login

    async def __aenter__(self) -> FakeBot:
        return self

    async def __aexit__(self, *exc) -> None:
        FakeBot.fechamentos += 1

    async def close(self) -> None:
        FakeBot.fechamentos += 1

    async def start(self, token: str) -> None:
        i = FakeBot.tentativas
        FakeBot.tentativas += 1
        resultado = FakeBot.roteiro[i] if i < len(FakeBot.roteiro) else None
        if resultado is not None:
            raise resultado


@pytest.fixture
def roteiro(monkeypatch):
    """Instala o duplo e devolve uma função que define o roteiro de falhas."""
    monkeypatch.setattr(entry, "Zenibot", FakeBot)
    monkeypatch.setattr(entry, "INITIAL_BACKOFF", 0.01)
    monkeypatch.setattr(entry, "MAX_BACKOFF", 0.02)
    # Nenhuma sessão do teste deve ser considerada estável.
    monkeypatch.setattr(entry, "STABLE_SESSION", 9999)

    def definir(falhas: list[BaseException | None]) -> None:
        FakeBot.tentativas = 0
        FakeBot.fechamentos = 0
        FakeBot.roteiro = falhas

    return definir


async def test_reconecta_apos_falha_de_dns(settings, roteiro) -> None:
    """O cenário real que derrubava o bot: sem DNS na partida, a exceção do
    login() subia e matava o processo."""
    roteiro([OSError("[Errno 11001] getaddrinfo failed"), None])
    await entry.supervise(settings)
    assert FakeBot.tentativas == 2


async def test_insiste_ate_conectar(settings, roteiro) -> None:
    roteiro([
        aiohttp.ClientConnectionError("recusada"),
        TimeoutError(),
        OSError("rede inacessivel"),
        None,
    ])
    await entry.supervise(settings)
    assert FakeBot.tentativas == 4


async def test_token_invalido_nao_e_repetido(settings, roteiro) -> None:
    """Repetir com credencial errada nunca resolveria: melhor sair avisando
    do que reconectar em laço para sempre."""
    roteiro([discord.LoginFailure("401"), None])
    with pytest.raises(discord.LoginFailure):
        await entry.supervise(settings)
    assert FakeBot.tentativas == 1


async def test_intent_faltando_nao_e_repetido(settings, roteiro) -> None:
    roteiro([discord.PrivilegedIntentsRequired(shard_id=None), None])
    with pytest.raises(discord.PrivilegedIntentsRequired):
        await entry.supervise(settings)
    assert FakeBot.tentativas == 1


async def test_encerramento_limpo_nao_reconecta(settings, roteiro) -> None:
    roteiro([None])
    await entry.supervise(settings)
    assert FakeBot.tentativas == 1


async def test_backoff_respeita_o_teto(settings, roteiro, monkeypatch) -> None:
    roteiro([OSError("x")] * 6 + [None])
    monkeypatch.setattr(entry, "MAX_BACKOFF", 0.03)

    inicio = asyncio.get_running_loop().time()
    await entry.supervise(settings)
    decorrido = asyncio.get_running_loop().time() - inicio

    assert FakeBot.tentativas == 7
    # Sem teto, 6 esperas dobrando passariam de 0,6s. Com teto ficam em ~0,15s;
    # a folga cobre o jitter de ±20%.
    assert decorrido < 0.4


async def test_recursos_liberados_a_cada_tentativa(settings, roteiro) -> None:
    roteiro([OSError("x"), OSError("y"), None])
    await entry.supervise(settings)
    assert FakeBot.fechamentos >= 3
