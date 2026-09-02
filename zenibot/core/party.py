"""Funções e renderização de uma party.

Tudo aqui é puro: dado o grupo, as vagas e os inscritos, produz o painel.
A parte que fala com o Discord fica no cog.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord

from zenibot.core import embeds

# Ordem de exibição, que é também a ordem de montagem de um grupo clássico.
FUNCOES: dict[str, tuple[str, str]] = {
    "tank": ("Tank", "🛡️"),
    "healer": ("Suporte", "💚"),
    "dps": ("Dano", "⚔️"),
    "livre": ("Participante", "👥"),
}


@dataclass(slots=True)
class Vaga:
    funcao: str
    capacidade: int
    inscritos: list[int]

    @property
    def cheia(self) -> bool:
        return len(self.inscritos) >= self.capacidade

    @property
    def rotulo(self) -> str:
        return FUNCOES[self.funcao][0]

    @property
    def emoji(self) -> str:
        return FUNCOES[self.funcao][1]


def total_inscritos(vagas: list[Vaga]) -> int:
    return sum(len(v.inscritos) for v in vagas)


def total_capacidade(vagas: list[Vaga]) -> int:
    return sum(v.capacidade for v in vagas)


def completa(vagas: list[Vaga]) -> bool:
    return bool(vagas) and all(v.cheia for v in vagas)


def montar_embed(
    *,
    titulo: str,
    descricao: str,
    dono_id: int,
    vagas: list[Vaga],
    inicio=None,
    encerrada: bool = False,
    event_url: str | None = None,
) -> discord.Embed:
    """Painel do grupo: uma seção por função, com quem já entrou."""
    ocupadas = total_inscritos(vagas)
    total = total_capacidade(vagas)

    if encerrada:
        cabecalho = "Grupo encerrado."
        cor = embeds.COLOR_ERROR
    elif completa(vagas):
        cabecalho = "**Grupo completo.**"
        cor = embeds.COLOR_OK
    else:
        cabecalho = f"**{ocupadas}/{total}** vagas preenchidas."
        cor = embeds.COLOR_INFO

    corpo = [cabecalho]
    if descricao:
        corpo.append(descricao)
    if inicio is not None:
        corpo.append(
            f"🗓️ {embeds.timestamp(inicio)} · {embeds.timestamp(inicio, 'R')}"
        )
    if event_url:
        corpo.append(f"[Evento no servidor]({event_url})")

    embed = discord.Embed(
        title=titulo, description="\n".join(corpo), color=cor
    )

    for vaga in vagas:
        nomes = "\n".join(f"<@{uid}>" for uid in vaga.inscritos) or "*vazio*"
        embed.add_field(
            name=f"{vaga.emoji} {vaga.rotulo} — {len(vaga.inscritos)}/{vaga.capacidade}",
            value=nomes[:1024],
            inline=True,
        )

    embed.set_footer(text=f"Organizado por · {dono_id}")
    return embed
