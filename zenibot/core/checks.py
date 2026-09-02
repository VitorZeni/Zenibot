"""Checagens de permissão e hierarquia.

Três camadas de autorização, todas necessárias:

1. `default_permissions()` no comando  -> o Discord esconde o comando. É UX.
2. Estas checagens no handler           -> segurança real.
3. Config por guild (staff_role_ids)    -> cargos de staff customizados.

A camada 1 sozinha NÃO é segurança: administradores podem reconfigurar
permissões de comandos pela UI do Discord.
"""

from __future__ import annotations

import discord
from discord import app_commands


class ZenibotError(app_commands.AppCommandError):
    """Erro esperado, com mensagem segura para exibir ao usuário."""


class NotStaff(ZenibotError):
    pass


class HierarchyError(ZenibotError):
    pass


async def e_staff(interaction: discord.Interaction) -> bool:
    """Manage Server, ou um dos cargos de staff configurados.

    Existe como função, e não só como decorador, porque callbacks de botão
    não passam pelo sistema de checks dos slash commands e precisavam
    reimplementar a mesma regra.
    """
    # Fora de guild não existe staff. Perguntar pela guild diz isso
    # diretamente; checar o tipo de `user` era só um proxy para a mesma
    # pergunta — dentro de uma guild ele já é Member por definição.
    if interaction.guild is None:
        return False
    membro = interaction.user
    if membro.guild_permissions.manage_guild:
        return True
    config = await interaction.client.db.get_config(interaction.guild_id)  # type: ignore[attr-defined]
    return any(cargo.id in config.staff_role_ids for cargo in membro.roles)


def is_staff():
    """Versão decorador de `e_staff`, para slash commands."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise ZenibotError("Este comando só funciona dentro de um servidor.")
        if await e_staff(interaction):
            return True
        raise NotStaff("Você não tem permissão para usar este comando.")

    return app_commands.check(predicate)


def is_owner():
    """Restrito aos ZENIBOT_OWNER_IDS — para /sync, /reload e afins."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in interaction.client.settings.owner_ids:  # type: ignore[attr-defined]
            return True
        raise NotStaff("Comando restrito aos donos do bot.")

    return app_commands.check(predicate)


def assert_can_moderate(
    actor: discord.Member,
    target: discord.Member,
    me: discord.Member,
) -> None:
    """Valida hierarquia antes de agir.

    Checar ANTES evita 403 da API — e o limite de 10.000 requisições inválidas
    em 10 minutos, que resulta em ban temporário de IP pela Cloudflare.
    """
    if target.id == actor.id:
        raise HierarchyError("Você não pode moderar a si mesmo.")
    if target.id == me.id:
        raise HierarchyError("Não posso moderar a mim mesmo.")
    if target.id == target.guild.owner_id:
        raise HierarchyError("Não é possível moderar o dono do servidor.")

    # O dono da guild ignora a checagem de hierarquia entre membros.
    if actor.id != actor.guild.owner_id and target.top_role >= actor.top_role:
        raise HierarchyError(
            "Você não pode moderar alguém com cargo igual ou superior ao seu."
        )
    if target.top_role >= me.top_role:
        raise HierarchyError(
            "Meu cargo está abaixo do cargo mais alto desse membro. "
            "Mova o cargo do Zenibot para cima na lista de cargos."
        )
