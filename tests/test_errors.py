"""Resposta de erro em componentes.

Regressão de um bug real: `View.on_error` do discord.py apenas registra no
log e nunca responde, então uma exceção num botão fazia o Discord exibir
"o aplicativo não respondeu a tempo" sem nenhuma explicação.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from zenibot.cogs.builder import BuilderView
from zenibot.cogs.selfroles import RoleToggleButton
from zenibot.core.checks import ZenibotError
from zenibot.core.errors import respond_error


class FakeResponse:
    def __init__(self, done: bool = False) -> None:
        self._done = done
        self.enviado: dict | None = None

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, **kwargs) -> None:
        self.enviado = kwargs
        self._done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.enviado: dict | None = None

    async def send(self, **kwargs) -> None:
        self.enviado = kwargs


class FakeInteraction:
    def __init__(self, done: bool = False) -> None:
        self.response = FakeResponse(done)
        self.followup = FakeFollowup()
        self.guild_id = 1
        self.user = SimpleNamespace(id=2)

    @property
    def resposta(self) -> dict | None:
        return self.response.enviado or self.followup.enviado


def texto(interaction: FakeInteraction) -> str:
    embed = interaction.resposta["embed"]
    return embed.description or ""


async def test_erro_esperado_vira_mensagem_clara() -> None:
    interaction = FakeInteraction()
    await respond_error(
        interaction, ZenibotError("Preciso de Enviar Mensagens."), contexto="teste"
    )
    assert "Enviar Mensagens" in texto(interaction)
    assert interaction.resposta["ephemeral"] is True


async def test_erro_inesperado_vira_id_de_correlacao() -> None:
    """O traceback nunca vai para o usuário: pode expor caminhos e queries."""
    interaction = FakeInteraction()
    await respond_error(interaction, RuntimeError("boom no banco"), contexto="teste")
    resposta = texto(interaction)
    assert "boom no banco" not in resposta
    assert "Referência" in resposta


async def test_usa_followup_quando_ja_respondido() -> None:
    interaction = FakeInteraction(done=True)
    await respond_error(interaction, ZenibotError("algo"), contexto="teste")
    assert interaction.response.enviado is None
    assert interaction.followup.enviado is not None


async def test_forbidden_explica_permissao() -> None:
    erro = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "missing permissions"
    )
    interaction = FakeInteraction()
    await respond_error(interaction, erro, contexto="teste")
    assert "permissão" in texto(interaction)


# ---------------------------------------------------------------------------
# Os componentes precisam ter o tratamento ligado
# ---------------------------------------------------------------------------


def test_painel_de_embed_sobrescreve_on_error() -> None:
    """Sem o override, a implementação padrão só loga e a interação morre."""
    assert BuilderView.on_error is not discord.ui.View.on_error


async def test_botao_de_self_role_responde_em_vez_de_estourar() -> None:
    """DynamicItem não passa pelo on_error de uma View, então o callback
    precisa tratar por conta própria."""
    botao = RoleToggleButton(123, label="Teste")
    interaction = FakeInteraction()
    # Fora de guild: o toggle levanta ZenibotError.
    interaction.guild = None
    interaction.user = SimpleNamespace(id=2)

    await botao.callback(interaction)

    assert interaction.resposta is not None, "o botão não respondeu à interação"
    assert "servidor" in texto(interaction)


@pytest.mark.parametrize("view_cls", [BuilderView])
def test_views_do_projeto_tem_on_error(view_cls) -> None:
    assert "on_error" in vars(view_cls)
