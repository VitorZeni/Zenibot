"""Detecção de raid por janela deslizante.

A janela vive só em memória, de propósito: um raid é, por definição, algo
acontecendo agora. Persistir o estado entre reinícios não traria valor — e
traria o risco oposto, de reagir a um pico que já passou.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any

import discord

log = logging.getLogger(__name__)

ACTIONS = ("alert", "lockdown")

# Nível aplicado durante o bloqueio: exige conta verificada por e-mail e com
# mais de 10 minutos no servidor.
LOCKDOWN_LEVEL = discord.VerificationLevel.high


class JoinWindow:
    """Entradas recentes por guild, para detectar picos.

    O limite por guild impede que um servidor movimentado faça a estrutura
    crescer sem fim — só as entradas dentro da janela importam.
    """

    def __init__(self, max_por_guild: int = 250) -> None:
        self._joins: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
        self._max = max_por_guild

    def record(self, guild_id: int, member_id: int, agora: float) -> None:
        fila = self._joins[guild_id]
        fila.append((agora, member_id))
        if len(fila) > self._max:
            fila.popleft()

    def recent(self, guild_id: int, janela_s: float, agora: float) -> list[int]:
        """IDs que entraram dentro da janela, descartando os antigos."""
        fila = self._joins[guild_id]
        while fila and agora - fila[0][0] > janela_s:
            fila.popleft()
        return [member_id for _, member_id in fila]

    def clear(self, guild_id: int) -> None:
        self._joins.pop(guild_id, None)


def snapshot(guild: discord.Guild) -> dict[str, Any]:
    """Estado a restaurar depois do bloqueio.

    Vai no payload do job persistente, então sobrevive a um restart no meio
    do bloqueio — sem isso, uma reinicialização deixaria o servidor trancado
    para sempre.
    """
    return {
        "verification_level": guild.verification_level.value,
        "invites_paused": guild.invites_paused(),
    }


async def apply_lockdown(guild: discord.Guild, reason: str) -> bool:
    """Pausa convites e eleva a verificação. False se faltar permissão."""
    if not guild.me.guild_permissions.manage_guild:
        log.warning(
            "Bloqueio anti-raid não aplicado na guild %s: falta Manage Server",
            guild.id,
        )
        return False
    try:
        await guild.edit(
            invites_disabled=True,
            verification_level=LOCKDOWN_LEVEL,
            reason=reason,
        )
    except discord.Forbidden:
        log.warning("Bloqueio anti-raid recusado na guild %s", guild.id)
        return False
    except discord.HTTPException:
        log.exception("Falha ao bloquear a guild %s", guild.id)
        return False
    return True


async def lift_lockdown(guild: discord.Guild, anterior: dict[str, Any]) -> bool:
    """Restaura o estado salvo em `snapshot()`."""
    try:
        nivel = discord.VerificationLevel(int(anterior.get("verification_level", 1)))
    except ValueError:
        nivel = discord.VerificationLevel.medium

    try:
        await guild.edit(
            invites_disabled=bool(anterior.get("invites_paused", False)),
            verification_level=nivel,
            reason="Anti-raid: fim do bloqueio",
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Falha ao liberar a guild %s", guild.id)
        return False
    log.info("Bloqueio anti-raid liberado na guild %s", guild.id)
    return True
