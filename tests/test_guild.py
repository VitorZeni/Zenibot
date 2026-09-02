"""Limites do Discord e checagem de canal publicável.

Extraído de cinco cópias espalhadas pelos cogs. A checagem ganhou uma
melhoria no caminho: agora nomeia exatamente quais permissões faltam, em vez
de listar as três sempre.
"""

from __future__ import annotations

import discord
import pytest

from zenibot.core.checks import ZenibotError
from zenibot.core.guild import (
    MAX_POR_CATEGORIA,
    categoria_cheia,
    exigir_publicavel,
    pode_publicar,
)


class CanalFalso:
    def __init__(self, **perms) -> None:
        self.mention = "<#1>"
        self._perms = discord.Permissions(**perms)

    def permissions_for(self, _membro) -> discord.Permissions:
        return self._perms


PUBLICAVEL = dict(view_channel=True, send_messages=True, embed_links=True)


def test_canal_completo_e_publicavel() -> None:
    assert pode_publicar(CanalFalso(**PUBLICAVEL), object()) is True


@pytest.mark.parametrize("faltando", ["view_channel", "send_messages", "embed_links"])
def test_qualquer_permissao_faltando_impede(faltando: str) -> None:
    perms = dict(PUBLICAVEL)
    perms[faltando] = False
    assert pode_publicar(CanalFalso(**perms), object()) is False


def test_exigir_nao_reclama_quando_esta_tudo_certo() -> None:
    exigir_publicavel(CanalFalso(**PUBLICAVEL), object())


def test_erro_nomeia_apenas_o_que_falta() -> None:
    """Listar as três sempre mandaria o admin conferir permissões que já tem."""
    canal = CanalFalso(view_channel=True, send_messages=True, embed_links=False)
    with pytest.raises(ZenibotError) as exc:
        exigir_publicavel(canal, object())

    mensagem = str(exc.value)
    assert "Inserir Links" in mensagem
    assert "Enviar Mensagens" not in mensagem
    assert "Ver Canal" not in mensagem


def test_erro_lista_varias_quando_faltam_varias() -> None:
    canal = CanalFalso(view_channel=True)
    mensagem = str(pytest.raises(ZenibotError, exigir_publicavel, canal, object()).value)
    assert "Enviar Mensagens" in mensagem
    assert "Inserir Links" in mensagem


# ---------------------------------------------------------------------------
# Categoria
# ---------------------------------------------------------------------------


class CategoriaFalsa:
    def __init__(self, quantos: int) -> None:
        self.channels = list(range(quantos))


def test_categoria_com_espaco() -> None:
    assert categoria_cheia(CategoriaFalsa(MAX_POR_CATEGORIA - 1)) is False


def test_categoria_no_limite_esta_cheia() -> None:
    assert categoria_cheia(CategoriaFalsa(MAX_POR_CATEGORIA)) is True


def test_sem_categoria_nunca_esta_cheia() -> None:
    """Canal fora de categoria responde ao teto da guild, não ao da categoria."""
    assert categoria_cheia(None) is False


def test_teto_bate_com_o_do_discord() -> None:
    assert MAX_POR_CATEGORIA == 50
