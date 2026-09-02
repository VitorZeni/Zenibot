"""Limites do Discord e checagens de canal.

`MAX_POR_CATEGORIA` estava replicado em três cogs — exatamente o tipo de
constante que fica desatualizada em uma cópia só.
"""

from __future__ import annotations

import discord

from zenibot.core.checks import ZenibotError

# Tetos do Discord.
MAX_POR_CATEGORIA = 50
MAX_CANAIS_POR_GUILD = 500


def pode_publicar(canal: discord.abc.GuildChannel, me: discord.Member) -> bool:
    """Se o bot consegue postar um embed neste canal."""
    perms = canal.permissions_for(me)
    return perms.view_channel and perms.send_messages and perms.embed_links


def exigir_publicavel(canal: discord.abc.GuildChannel, me: discord.Member) -> None:
    """Levanta ZenibotError nomeando as permissões que faltam."""
    if pode_publicar(canal, me):
        return
    perms = canal.permissions_for(me)
    faltando = [
        nome
        for nome, tem in (
            ("Ver Canal", perms.view_channel),
            ("Enviar Mensagens", perms.send_messages),
            ("Inserir Links", perms.embed_links),
        )
        if not tem
    ]
    raise ZenibotError(
        f"Preciso de **{'**, **'.join(faltando)}** em {canal.mention}."
    )


def categoria_cheia(categoria: discord.CategoryChannel | None) -> bool:
    return categoria is not None and len(categoria.channels) >= MAX_POR_CATEGORIA
