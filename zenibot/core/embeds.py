"""Factories de embed e constantes de apresentação.

NO_MENTIONS é o detalhe mais importante deste módulo: sem allowed_mentions
explícito, um log de mensagem apagada re-notifica todo mundo que ela mencionava
e um /say vira ferramenta de mass-ping.
"""

from __future__ import annotations

from datetime import datetime

import discord

NO_MENTIONS = discord.AllowedMentions.none()

COLOR_OK = discord.Color.from_str("#43b581")
COLOR_WARN = discord.Color.from_str("#faa61a")
COLOR_ERROR = discord.Color.from_str("#f04747")
COLOR_INFO = discord.Color.from_str("#5865f2")


def ok(description: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR_OK)


def warn(description: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR_WARN)


def error(description: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR_ERROR)


def info(description: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=COLOR_INFO)


def timestamp(dt: datetime, style: str = "F") -> str:
    """Timestamp dinâmico do Discord: renderiza no fuso local de cada leitor.

    Estilos: t (hora) T (hora+seg) d (data) D (data extensa)
             f (data+hora) F (completo) R (relativo)
    """
    return f"<t:{int(dt.timestamp())}:{style}>"


def case_embed(
    *,
    case_number: int,
    action: str,
    target: discord.abc.User,
    moderator: discord.abc.User,
    reason: str,
    duration: str | None = None,
) -> discord.Embed:
    colors = {
        "warn": COLOR_WARN,
        "timeout": COLOR_WARN,
        "kick": COLOR_ERROR,
        "ban": COLOR_ERROR,
        "unban": COLOR_OK,
    }
    embed = discord.Embed(
        title=f"Caso #{case_number} · {action.capitalize()}",
        color=colors.get(action, COLOR_INFO),
    )
    embed.add_field(name="Usuário", value=f"{target.mention}\n`{target.id}`", inline=True)
    embed.add_field(name="Moderador", value=moderator.mention, inline=True)
    if duration:
        embed.add_field(name="Duração", value=duration, inline=True)
    embed.add_field(name="Motivo", value=reason[:1024], inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    return embed
