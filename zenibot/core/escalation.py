"""Motor de escalonamento automático de punições.

A régua é por servidor: "na 3ª infração, timeout de 1h; na 5ª, ban de 7 dias".

Duas decisões que evitam comportamento surpreendente:

1. **A regra dispara no valor exato do limiar**, não em "maior ou igual".
   Com `>=`, um servidor com regra em 3 puniria de novo na 4ª, na 5ª e em
   toda infração seguinte, sempre com a mesma pena — o oposto de escalonar.

2. **A punição gerada não conta como infração** (`automatic=1` no caso).
   Contá-la faria a régua andar sozinha: a punição da 3ª viraria a 4ª
   infração e a próxima advertência cairia numa regra mais dura do que a
   merecida.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import discord

from zenibot.core import embeds
from zenibot.core.db import EscalationRule, now
from zenibot.core.duration import MAX_TIMEOUT, humanize

if TYPE_CHECKING:
    from zenibot.bot import Zenibot

log = logging.getLogger(__name__)

ACTIONS = ("timeout", "kick", "ban")


def rule_for_count(rules: list[EscalationRule], count: int) -> EscalationRule | None:
    """Regra cujo limiar é exatamente a contagem atual."""
    for rule in rules:
        if rule.threshold == count:
            return rule
    return None


def describe(rule: EscalationRule) -> str:
    if rule.action == "timeout":
        return f"timeout de {humanize(timedelta(seconds=rule.duration_s or 0))}"
    if rule.action == "kick":
        return "expulsão"
    if rule.duration_s:
        return f"ban de {humanize(timedelta(seconds=rule.duration_s))}"
    return "ban permanente"


def validate(action: str, duration_s: int | None) -> None:
    """Levanta ValueError se a combinação ação/duração não faz sentido."""
    if action not in ACTIONS:
        raise ValueError(f"Ação desconhecida: {action}")
    if action == "timeout":
        if not duration_s:
            raise ValueError("Timeout exige duração.")
        if duration_s > MAX_TIMEOUT.total_seconds():
            raise ValueError("O Discord limita o timeout a 28 dias.")
    if action == "kick" and duration_s:
        raise ValueError("Expulsão não tem duração.")


async def apply_rule(
    bot: Zenibot,
    member: discord.Member,
    rule: EscalationRule,
    *,
    count: int,
) -> str | None:
    """Executa a punição da regra. Devolve a descrição, ou None se falhou."""
    guild = member.guild
    acao = describe(rule)
    motivo = f"Escalonamento automático: {count}ª infração"

    # Hierarquia primeiro: sem isto tomamos 403 e o caso ficaria registrado
    # como se a punição tivesse sido aplicada.
    if member.top_role >= guild.me.top_role or member.id == guild.owner_id:
        log.warning(
            "Escalonamento não aplicado na guild %s: hierarquia impede moderar %s",
            guild.id,
            member.id,
        )
        return None

    try:
        await member.send(
            embed=embeds.warn(
                f"**Servidor:** {guild.name}\n"
                f"**Motivo:** {motivo}\n"
                f"**Punição:** {acao}",
                title="Punição automática",
            )
        )
    except (discord.Forbidden, discord.HTTPException):
        pass  # DMs fechadas — não impede a punição

    try:
        if rule.action == "timeout":
            await member.timeout(timedelta(seconds=rule.duration_s or 0), reason=motivo)
        elif rule.action == "kick":
            await member.kick(reason=motivo)
        else:
            await guild.ban(member, reason=motivo, delete_message_seconds=0)
    except discord.Forbidden:
        log.warning("Sem permissão para aplicar %s na guild %s", rule.action, guild.id)
        return None
    except discord.HTTPException:
        log.exception("Falha ao aplicar escalonamento na guild %s", guild.id)
        return None

    await bot.db.add_case(
        guild_id=guild.id,
        user_id=member.id,
        moderator_id=bot.user.id,
        action=rule.action,
        reason=motivo,
        duration_s=rule.duration_s,
        automatic=True,
    )

    if rule.action == "ban" and rule.duration_s:
        await bot.db.schedule(
            guild_id=guild.id,
            user_id=member.id,
            kind="unban",
            run_at=now() + timedelta(seconds=rule.duration_s),
            payload={"origem": "escalonamento"},
        )

    log.info(
        "Escalonamento aplicado na guild %s: %s em %s (%dª infração)",
        guild.id,
        rule.action,
        member.id,
        count,
    )
    return acao
