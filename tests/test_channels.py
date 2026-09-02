"""Canais privados por cargo: normalização de nome e montagem de permissões."""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from zenibot.cogs.channels import (
    MAX_CARGOS,
    permissoes_para,
    sanitizar_nome,
    validar_cargos,
)
from zenibot.core.checks import ZenibotError

# ---------------------------------------------------------------------------
# Nome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Sala da Staff", "sala-da-staff"),
        ("  espaços  ", "espaços"),
        ("Já-Com-Traço", "já-com-traço"),
        ("pontuação!!! demais???", "pontuação-demais"),
        ("muitos---traços", "muitos-traços"),
        ("--nas-pontas--", "nas-pontas"),
        ("under_score", "under_score"),
    ],
)
def test_nome_de_texto_normalizado(entrada: str, esperado: str) -> None:
    """Normalizar aqui faz a prévia bater com o que o Discord vai criar, em
    vez de o servidor 'corrigir' o nome depois e surpreender quem criou."""
    assert sanitizar_nome(entrada, voz=False) == esperado


def test_nome_de_texto_vazio_ganha_padrao() -> None:
    assert sanitizar_nome("!!!", voz=False) == "canal"
    assert sanitizar_nome("   ", voz=False) == "canal"


def test_nome_de_voz_preserva_maiusculas_e_espacos() -> None:
    assert sanitizar_nome("Sala da Staff", voz=True) == "Sala da Staff"
    assert sanitizar_nome("   ", voz=True) == "sala"


def test_nome_respeita_o_limite_do_discord() -> None:
    assert len(sanitizar_nome("a" * 300, voz=False)) <= 100
    assert len(sanitizar_nome("A" * 300, voz=True)) <= 100


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------

# Classes em vez de SimpleNamespace porque overwrites é um dicionário e
# SimpleNamespace não é hashable — objetos do discord.py são.


class CargoFalso:
    def __init__(self, nome: str = "Staff", *, padrao: bool = False) -> None:
        self.name = nome
        self.mention = f"@{nome}"
        self._padrao = padrao

    def is_default(self) -> bool:
        return self._padrao


class MembroFalso:
    def __init__(self, nome: str = "vitor", **perms) -> None:
        self.name = nome
        self.mention = f"@{nome}"
        self.guild_permissions = discord.Permissions(**perms)


def cargo_falso(nome: str = "Staff", *, padrao: bool = False) -> CargoFalso:
    return CargoFalso(nome, padrao=padrao)


def guild_falsa(*, manage_roles: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        default_role=cargo_falso("@everyone", padrao=True),
        me=MembroFalso("Zenibot", manage_roles=manage_roles),
    )


def test_everyone_perde_a_visao() -> None:
    guild = guild_falsa()
    overwrites = permissoes_para(guild, [cargo_falso()], MembroFalso(), voz=False)
    assert overwrites[guild.default_role].view_channel is False


def test_cargos_escolhidos_enxergam() -> None:
    guild = guild_falsa()
    staff = cargo_falso()
    overwrites = permissoes_para(guild, [staff], MembroFalso(), voz=False)
    assert overwrites[staff].view_channel is True
    assert overwrites[staff].send_messages is True


def test_autor_entra_na_lista() -> None:
    """Sem isto dá para criar um canal que quem criou não consegue ver."""
    guild = guild_falsa()
    autor = MembroFalso("vitor")
    overwrites = permissoes_para(guild, [cargo_falso()], autor, voz=False)
    assert autor in overwrites
    assert overwrites[autor].view_channel is True


def test_bot_se_inclui_para_administrar_depois() -> None:
    guild = guild_falsa()
    overwrites = permissoes_para(guild, [cargo_falso()], MembroFalso(), voz=False)
    assert overwrites[guild.me].manage_channels is True


def test_canal_de_voz_ganha_conectar_e_falar() -> None:
    guild = guild_falsa()
    staff = cargo_falso()
    overwrites = permissoes_para(guild, [staff], MembroFalso(), voz=True)
    assert overwrites[staff].connect is True
    assert overwrites[staff].speak is True
    assert overwrites[staff].send_messages is None


def test_canal_de_texto_nao_ganha_permissao_de_voz() -> None:
    guild = guild_falsa()
    staff = cargo_falso()
    overwrites = permissoes_para(guild, [staff], MembroFalso(), voz=False)
    assert overwrites[staff].connect is None


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def test_recusa_everyone() -> None:
    """Conceder a @everyone deixaria o canal público — o oposto do comando."""
    with pytest.raises(ZenibotError, match="everyone"):
        validar_cargos([cargo_falso("@everyone", padrao=True)], guild_falsa())


def test_recusa_cargos_demais() -> None:
    demais = [cargo_falso(f"c{i}") for i in range(MAX_CARGOS + 1)]
    with pytest.raises(ZenibotError):
        validar_cargos(demais, guild_falsa())


def test_exige_gerenciar_cargos() -> None:
    with pytest.raises(ZenibotError, match="Gerenciar Cargos"):
        validar_cargos([cargo_falso()], guild_falsa(manage_roles=False))


def test_aceita_o_caso_normal() -> None:
    validar_cargos([cargo_falso("Staff"), cargo_falso("Mod")], guild_falsa())
