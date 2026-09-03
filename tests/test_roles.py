"""Gestão de cargos: validação e as travas de escalada de privilégio."""

from __future__ import annotations

import discord
import pytest

from tests.fakes import FakeGuild, FakeMember, FakeRole
from zenibot.core import roles
from zenibot.core.checks import ZenibotError

# ---------------------------------------------------------------------------
# Nome
# ---------------------------------------------------------------------------


def test_nome_normal_passa() -> None:
    assert roles.validar_nome("  Raider  ") == "Raider"


@pytest.mark.parametrize("nome", ["", "   ", "\n"])
def test_nome_vazio_e_recusado(nome: str) -> None:
    with pytest.raises(ZenibotError, match="precisa de um nome"):
        roles.validar_nome(nome)


def test_nome_longo_demais_e_recusado() -> None:
    with pytest.raises(ZenibotError, match="100"):
        roles.validar_nome("x" * 101)


@pytest.mark.parametrize("nome", ["@everyone", "everyone", "@here", "HERE"])
def test_nomes_reservados_sao_recusados(nome: str) -> None:
    with pytest.raises(ZenibotError, match="reservado"):
        roles.validar_nome(nome)


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------


class GuildComCargos(FakeGuild):
    def __init__(self, quantos: int, **kw) -> None:
        super().__init__(**kw)
        self.roles = [FakeRole(i, f"c{i}", i) for i in range(quantos)]


def test_criar_exige_gerenciar_cargos() -> None:
    guild = FakeGuild()
    guild.me = FakeMember(999, "Zenibot", manage_roles=False)
    guild.roles = []
    with pytest.raises(ZenibotError, match="Gerenciar Cargos"):
        roles.assert_pode_criar(guild)


def test_criar_respeita_o_teto_do_discord() -> None:
    """250 cargos é o limite do servidor; passar disso devolveria erro da API."""
    guild = GuildComCargos(roles.MAX_CARGOS)
    with pytest.raises(ZenibotError, match="250"):
        roles.assert_pode_criar(guild)


def test_criar_com_espaco_sobrando_passa() -> None:
    roles.assert_pode_criar(GuildComCargos(roles.MAX_CARGOS - 1))


# ---------------------------------------------------------------------------
# Edição e exclusão
# ---------------------------------------------------------------------------


def cenario(*, posicao_alvo: int = 10, ator_pos: int = 50, dono: bool = False):
    """Bot na posição 100; ator e alvo abaixo, salvo indicação contrária."""
    guild = FakeGuild(owner_id=1 if dono else 555)
    alvo = FakeRole(500, "Alvo", posicao_alvo)
    ator = FakeMember(1, "mod", roles=[FakeRole(9, "Staff", ator_pos)])
    ator.guild = guild
    return guild, ator, alvo


def test_cargo_abaixo_de_todos_e_gerenciavel() -> None:
    guild, ator, alvo = cenario()
    roles.assert_gerenciavel(alvo, ator, guild.me)


def test_everyone_nao_e_gerenciavel() -> None:
    guild, ator, _ = cenario()
    with pytest.raises(ZenibotError, match="everyone"):
        roles.assert_gerenciavel(guild.default_role, ator, guild.me)


def test_cargo_de_integracao_nao_e_gerenciavel() -> None:
    """Quem controla é o app dono dele, não o Zenibot."""
    guild, ator, _ = cenario()
    gerenciado = FakeRole(600, "OutroBot", 5, managed=True)
    with pytest.raises(ZenibotError, match="integração"):
        roles.assert_gerenciavel(gerenciado, ator, guild.me)


def test_cargo_acima_do_bot_e_recusado() -> None:
    guild, ator, _ = cenario()
    alto = FakeRole(700, "Admin", 200)  # bot está em 100
    with pytest.raises(ZenibotError, match="meu nível"):
        roles.assert_gerenciavel(alto, ator, guild.me)


def test_moderador_nao_mexe_em_cargo_do_proprio_nivel() -> None:
    """Sem esta trava, um moderador poderia renomear ou apagar o cargo de
    administrador — escalada de privilégio pelo bot."""
    guild, ator, alvo = cenario(posicao_alvo=50, ator_pos=50)
    with pytest.raises(ZenibotError, match="seu nível"):
        roles.assert_gerenciavel(alvo, ator, guild.me)


def test_moderador_nao_mexe_em_cargo_acima_do_seu() -> None:
    guild, ator, alvo = cenario(posicao_alvo=80, ator_pos=50)
    with pytest.raises(ZenibotError, match="seu nível"):
        roles.assert_gerenciavel(alvo, ator, guild.me)


def test_dono_do_servidor_ignora_a_trava_de_nivel() -> None:
    """O dono não tem teto de hierarquia no Discord; o bot ainda tem."""
    guild, ator, alvo = cenario(posicao_alvo=80, ator_pos=50, dono=True)
    roles.assert_gerenciavel(alvo, ator, guild.me)


def test_bot_sem_permissao_e_recusado_antes_da_api() -> None:
    guild, ator, alvo = cenario()
    guild.me = FakeMember(999, "Zenibot", roles=[FakeRole(900, "Z", 100)],
                          manage_roles=False)
    with pytest.raises(ZenibotError, match="Gerenciar Cargos"):
        roles.assert_gerenciavel(alvo, ator, guild.me)


# ---------------------------------------------------------------------------
# Descrição de permissões
# ---------------------------------------------------------------------------


def test_administrator_e_destacado() -> None:
    """Antes de apagar, quem confirma precisa saber que o cargo era admin."""
    admin = FakeRole(1, "Admin", 5, administrator=True)
    assert "Administrador" in roles.descreve_permissoes(admin)


def test_cargo_sem_permissao() -> None:
    assert "nenhuma" in roles.descreve_permissoes(FakeRole(1, "Cosmético", 5))


def test_conta_as_permissoes_ativas() -> None:
    cargo = FakeRole(1, "Mod", 5, kick_members=True, ban_members=True)
    assert "2 permissão" in roles.descreve_permissoes(cargo)


def test_cargo_criado_nasce_sem_permissao() -> None:
    """A decisão de escopo: o comando não é atalho para conceder poder."""
    assert discord.Permissions.none().value == 0
