"""Grupos com vagas por função — party e raide.

Um painel por grupo, um botão por função. Entrar, sair e trocar de função
acontecem no clique, e o painel se redesenha.

O ponto delicado é a última vaga: dois cliques simultâneos não podem passar
os dois. A checagem de lotação e a inserção acontecem na mesma instrução SQL
(ver `Database.join_party`), então o banco decide o vencedor em vez de a
última escrita ganhar.

Reaproveita duas coisas prontas: os botões usam `DynamicItem`, como os
self-roles, então painéis antigos voltam a responder após restart; e o
lembrete entra na fila persistente de jobs, ganhando só um `kind` novo.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import discord
from discord import app_commands, ui
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds, party
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.db import now
from zenibot.core.duration import parse_duration
from zenibot.core.errors import respond_error
from zenibot.core.guild import exigir_publicavel

log = logging.getLogger(__name__)

# Quanto antes do horário o lembrete é disparado.
ANTECEDENCIA = timedelta(minutes=15)

MAX_POR_FUNCAO = 40
LIMITE_TITULO = 100
LIMITE_DESCRICAO = 500


class PartyButton(
    ui.DynamicItem[ui.Button],
    template=r"party:(?P<acao>entrar|encerrar):(?P<party>\d+):(?P<funcao>[a-z]*)",
):
    def __init__(
        self,
        acao: str,
        party_id: int,
        funcao: str = "",
        *,
        rotulo: str = "",
        emoji: str | None = None,
        cheia: bool = False,
    ) -> None:
        if acao == "encerrar":
            botao = ui.Button(
                label="Encerrar",
                style=discord.ButtonStyle.danger,
                custom_id=f"party:encerrar:{party_id}:",
            )
        else:
            botao = ui.Button(
                label=rotulo,
                emoji=emoji,
                # Cheia fica secundária, mas continua clicável: é assim que se
                # sai de uma função lotada em que você já está.
                style=discord.ButtonStyle.secondary
                if cheia
                else discord.ButtonStyle.primary,
                custom_id=f"party:entrar:{party_id}:{funcao}",
            )
        super().__init__(botao)
        self.acao = acao
        self.party_id = party_id
        self.funcao = funcao

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match[str],
        /,
    ) -> PartyButton:
        return cls(
            match["acao"],
            int(match["party"]),
            match["funcao"],
            rotulo=item.label or "",
            emoji=item.emoji,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # DynamicItem não passa pelo on_error de uma View.
        try:
            cog: Party = interaction.client.get_cog("Party")
            if self.acao == "encerrar":
                await cog.encerrar_pelo_botao(interaction, self.party_id)
            else:
                await cog.alternar(interaction, self.party_id, self.funcao)
        except Exception as exc:  # noqa: BLE001 — o handler classifica
            await respond_error(interaction, exc, contexto="botão de party")


def montar_view(party_id: int, vagas: list[party.Vaga], encerrada: bool) -> ui.View:
    view = ui.View(timeout=None)
    if encerrada:
        return view
    for vaga in vagas:
        view.add_item(
            PartyButton(
                "entrar",
                party_id,
                vaga.funcao,
                rotulo=vaga.rotulo,
                emoji=vaga.emoji,
                cheia=vaga.cheia,
            )
        )
    view.add_item(PartyButton("encerrar", party_id))
    return view


class Party(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------

    async def carregar(self, party_id: int):
        """Grupo + vagas preenchidas, prontos para renderizar."""
        grupo = await self.bot.db.get_party(party_id)
        if grupo is None:
            raise ZenibotError("Esse grupo não existe mais.")

        inscritos = await self.bot.db.party_members(party_id)
        vagas = [
            party.Vaga(
                funcao=funcao,
                capacidade=capacidade,
                inscritos=[u for u, f in inscritos if f == funcao],
            )
            for funcao, capacidade in await self.bot.db.party_slots(party_id)
        ]
        # Ordem estável de exibição, independente da ordem do banco.
        vagas.sort(key=lambda v: list(party.FUNCOES).index(v.funcao))
        return grupo, vagas

    async def redesenhar(self, interaction: discord.Interaction, party_id: int) -> None:
        grupo, vagas = await self.carregar(party_id)
        encerrada = grupo.status != "aberta"
        evento_url = None
        if grupo.event_id:
            evento_url = (
                f"https://discord.com/events/{grupo.guild_id}/{grupo.event_id}"
            )

        embed = party.montar_embed(
            titulo=grupo.titulo,
            descricao=grupo.descricao,
            dono_id=grupo.owner_id,
            vagas=vagas,
            inicio=grupo.inicio,
            encerrada=encerrada,
            event_url=evento_url,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=montar_view(party_id, vagas, encerrada),
            allowed_mentions=embeds.NO_MENTIONS,
        )

    async def alternar(
        self, interaction: discord.Interaction, party_id: int, funcao: str
    ) -> None:
        grupo = await self.bot.db.get_party(party_id)
        if grupo is None:
            raise ZenibotError("Esse grupo não existe mais.")
        if grupo.status != "aberta":
            raise ZenibotError("Este grupo já foi encerrado.")

        resultado = await self.bot.db.join_party(party_id, interaction.user.id, funcao)
        if resultado == "cheia":
            rotulo = party.FUNCOES[funcao][0]
            raise ZenibotError(
                f"As vagas de **{rotulo}** acabaram de encher. "
                "Você continua na função em que estava."
            )

        await self.redesenhar(interaction, party_id)

    async def encerrar_pelo_botao(
        self, interaction: discord.Interaction, party_id: int
    ) -> None:
        grupo = await self.bot.db.get_party(party_id)
        if grupo is None:
            raise ZenibotError("Esse grupo não existe mais.")
        if interaction.user.id != grupo.owner_id and not (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild
        ):
            raise ZenibotError("Só quem organizou o grupo pode encerrá-lo.")

        await self.bot.db.close_party(party_id)
        await self.bot.db.cancel_jobs(
            guild_id=grupo.guild_id, user_id=grupo.owner_id, kind="party_reminder"
        )
        await self.redesenhar(interaction, party_id)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    grupo = app_commands.Group(
        name="grupo",
        description="Montagem de grupos com vagas por função",
        guild_only=True,
    )

    @grupo.command(name="criar", description="Publica um painel de montagem de grupo")
    @app_commands.describe(
        titulo="Nome da atividade",
        descricao="Detalhes (opcional)",
        quando="Daqui a quanto tempo começa. Ex.: 2h, 30m",
        tank="Vagas de tank",
        suporte="Vagas de suporte",
        dano="Vagas de dano",
        livre="Vagas sem função definida",
        evento="Criar também um Evento Agendado do servidor",
    )
    @is_staff()
    async def criar(
        self,
        interaction: discord.Interaction,
        titulo: app_commands.Range[str, 1, LIMITE_TITULO],
        descricao: app_commands.Range[str, 1, LIMITE_DESCRICAO] | None = None,
        quando: str | None = None,
        tank: app_commands.Range[int, 0, MAX_POR_FUNCAO] = 0,
        suporte: app_commands.Range[int, 0, MAX_POR_FUNCAO] = 0,
        dano: app_commands.Range[int, 0, MAX_POR_FUNCAO] = 0,
        livre: app_commands.Range[int, 0, MAX_POR_FUNCAO] = 0,
        evento: bool = False,
    ) -> None:
        vagas = {
            funcao: n
            for funcao, n in (
                ("tank", tank), ("healer", suporte), ("dps", dano), ("livre", livre)
            )
            if n > 0
        }
        if not vagas:
            # Sem nenhuma vaga o painel não teria botão nenhum; 5 participantes
            # é o formato mais comum de grupo casual.
            vagas = {"livre": 5}

        inicio = now() + parse_duration(quando) if quando else None
        if evento and inicio is None:
            raise ZenibotError(
                "Para criar um Evento Agendado é preciso informar `quando`."
            )

        exigir_publicavel(interaction.channel, interaction.guild.me)
        await interaction.response.defer(ephemeral=True)

        party_id = await self.bot.db.create_party(
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id,
            titulo=titulo,
            descricao=descricao or "",
            inicio=inicio,
            vagas=vagas,
        )

        try:
            evento_url = None
            if evento:
                evento_url = await self.criar_evento(
                    interaction, titulo, descricao, inicio, party_id
                )

            _grupo, lista = await self.carregar(party_id)
            mensagem = await interaction.channel.send(
                embed=party.montar_embed(
                    titulo=titulo,
                    descricao=descricao or "",
                    dono_id=interaction.user.id,
                    vagas=lista,
                    inicio=inicio,
                    event_url=evento_url,
                ),
                view=montar_view(party_id, lista, encerrada=False),
                allowed_mentions=embeds.NO_MENTIONS,
            )
        except discord.HTTPException:
            # Sem isto sobraria um grupo órfão, sem painel, ocupando um id.
            await self.bot.db.drop_party(party_id)
            log.exception("Falha ao publicar painel de grupo na guild %s", interaction.guild_id)
            raise ZenibotError("Não consegui publicar o painel.") from None

        await self.bot.db.set_party_message(
            party_id, interaction.channel.id, mensagem.id
        )

        if inicio is not None:
            # Reaproveita a fila persistente: um lembrete agendado sobrevive a
            # restart, ao contrário de um sleep em memória.
            await self.bot.db.schedule(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel.id,
                user_id=interaction.user.id,
                kind="party_reminder",
                run_at=inicio - ANTECEDENCIA,
                payload={"party": party_id},
            )

        await interaction.followup.send(
            embed=embeds.ok(f"Grupo publicado — {mensagem.jump_url}"), ephemeral=True
        )

    async def criar_evento(
        self, interaction: discord.Interaction, titulo, descricao, inicio, party_id
    ) -> str | None:
        """Evento Agendado nativo: aparece na aba de eventos do servidor.

        Usa o intent `guild_scheduled_events`, ligado desde o primeiro dia e
        até agora sem uso.
        """
        if not interaction.guild.me.guild_permissions.manage_events:
            raise ZenibotError(
                "Preciso da permissão **Gerenciar Eventos** para criar o evento "
                "do servidor. Publique sem `evento:True` ou ajuste a permissão."
            )
        try:
            criado = await interaction.guild.create_scheduled_event(
                name=titulo[:100],
                description=(descricao or "Grupo montado pelo Zenibot")[:1000],
                start_time=inicio,
                # Externo evita exigir um canal de voz e pede fim explícito.
                end_time=inicio + timedelta(hours=2),
                entity_type=discord.EntityType.external,
                location=f"#{interaction.channel.name}",
                privacy_level=discord.PrivacyLevel.guild_only,
                reason=f"Grupo criado por {interaction.user}",
            )
        except discord.HTTPException:
            log.exception("Falha ao criar evento na guild %s", interaction.guild_id)
            raise ZenibotError("Não consegui criar o Evento Agendado.") from None

        await self.bot.db.set_party_event(party_id, criado.id)
        return criado.url


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Party(bot))
    bot.add_dynamic_items(PartyButton)
