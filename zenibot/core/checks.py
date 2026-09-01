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


def is_staff():
    """Passa se o membro tem Manage Server ou um cargo de staff configurado."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            raise ZenibotError("Este comando só funciona dentro de um servidor.")
        if interaction.user.guild_permissions.manage_guild:
            return True

        config = await interaction.client.db.get_config(interaction.guild_id)  # type: ignore[attr-defined]
        if any(role.id in config.staff_role_ids for role in interaction.user.roles):
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
