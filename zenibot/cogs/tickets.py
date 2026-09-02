"""Sistema de tickets: um canal privado por atendimento.

Um painel fixo com o botão "Abrir ticket"; cada clique cria um canal visível
apenas para quem abriu e para a equipe. Dentro dele, botões para assumir,
fechar e apagar.

Decisões que sustentam o recurso:

1. **Um ticket aberto por pessoa.** Sem essa trava, clique repetido no painel
   enche o servidor de canais — e o teto do Discord é 500 por servidor.

2. **Assumir é atômico no banco.** Dois membros da equipe clicando ao mesmo
   tempo não podem ambos "assumir": o UPDATE só vale se ninguém tiver
   assumido antes.

3. **Fechar não apaga.** Revoga o acesso de quem abriu e mantém o canal para
   a equipe reler; apagar é um segundo passo, deliberado. O registro do
   ticket sobrevive ao canal, para o histórico não sumir junto.

Os botões usam `DynamicItem`, então painéis e tickets antigos continuam
funcionando depois de um restart, sem nada carregado na inicialização.
"""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands, ui
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.db import Ticket
from zenibot.core.errors import respond_error

log = logging.getLogger(__name__)

MAX_POR_CATEGORIA = 50
LIMITE_ASSUNTO = 100


def nome_do_canal(numero: int) -> str:
    return f"ticket-{numero:04d}"


async def eh_staff(interaction: discord.Interaction) -> bool:
    """Mesma regra dos comandos: Gerenciar Servidor ou cargo configurado."""
    membro = interaction.user
    if not isinstance(membro, discord.Member):
        return False
    if membro.guild_permissions.manage_guild:
        return True
    cfg = await interaction.client.db.get_config(interaction.guild_id)
    return any(cargo.id in cfg.staff_role_ids for cargo in membro.roles)


class TicketButton(
    ui.DynamicItem[ui.Button],
    template=r"ticket:(?P<acao>abrir|assumir|fechar|apagar):(?P<alvo>\d+)",
):
    """Um botão para as quatro ações; a ação vem do próprio custom_id."""

    ESTILOS = {
        "abrir": (discord.ButtonStyle.primary, "Abrir ticket", "🎫"),
        "assumir": (discord.ButtonStyle.secondary, "Assumir", "🙋"),
        "fechar": (discord.ButtonStyle.secondary, "Fechar", "🔒"),
        "apagar": (discord.ButtonStyle.danger, "Apagar canal", "🗑️"),
    }

    def __init__(self, acao: str, alvo: int = 0, *, disabled: bool = False) -> None:
        estilo, rotulo, emoji = self.ESTILOS[acao]
        super().__init__(
            ui.Button(
                label=rotulo,
                emoji=emoji,
                style=estilo,
                disabled=disabled,
                custom_id=f"ticket:{acao}:{alvo}",
            )
        )
        self.acao = acao
        self.alvo = alvo

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: ui.Button,
        match: re.Match[str],
        /,
    ) -> TicketButton:
        return cls(match["acao"], int(match["alvo"]), disabled=item.disabled)

    async def callback(self, interaction: discord.Interaction) -> None:
        # DynamicItem não passa pelo on_error de uma View, então o tratamento
        # fica aqui — senão o usuário veria "não respondeu a tempo".
        try:
            cog: Tickets = interaction.client.get_cog("Tickets")
            await getattr(cog, f"acao_{self.acao}")(interaction, self.alvo)
        except Exception as exc:  # noqa: BLE001 — o handler classifica
            await respond_error(interaction, exc, contexto=f"ticket:{self.acao}")


def painel_view() -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(TicketButton("abrir"))
    return view


def ticket_view(ticket: Ticket) -> ui.View:
    view = ui.View(timeout=None)
    if ticket.status == "aberto":
        view.add_item(
            TicketButton("assumir", ticket.id, disabled=ticket.claimed_by is not None)
        )
        view.add_item(TicketButton("fechar", ticket.id))
    else:
        view.add_item(TicketButton("apagar", ticket.id))
    return view


class AssuntoModal(ui.Modal, title="Abrir ticket"):
    assunto = ui.TextInput(
        label="Sobre o que é?",
        style=discord.TextStyle.paragraph,
        max_length=LIMITE_ASSUNTO,
        placeholder="Descreva em uma frase o que você precisa.",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class Tickets(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Ações dos botões
    # ------------------------------------------------------------------

    async def acao_abrir(self, interaction: discord.Interaction, _alvo: int) -> None:
        guild = interaction.guild
        cfg = await self.bot.db.get_config(guild.id)

        aberto = await self.bot.db.open_ticket_of(guild.id, interaction.user.id)
        if aberto is not None:
            destino = (
                f"<#{aberto.channel_id}>" if aberto.channel_id else "um canal já criado"
            )
            raise ZenibotError(
                f"Você já tem o ticket **#{aberto.numero}** aberto em {destino}. "
                "Feche-o antes de abrir outro."
            )

        if not guild.me.guild_permissions.manage_channels:
            raise ZenibotError(
                "Não consigo criar canais — falta a permissão **Gerenciar Canais**."
            )

        categoria = guild.get_channel(cfg.ticket_category_id) if cfg.ticket_category_id else None
        if categoria is not None and len(categoria.channels) >= MAX_POR_CATEGORIA:
            raise ZenibotError(
                "A categoria de tickets está cheia. Avise a equipe."
            )

        modal = AssuntoModal()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return

        assunto = str(modal.assunto).strip()
        ticket_id, numero = await self.bot.db.open_ticket(
            guild_id=guild.id, opener_id=interaction.user.id, assunto=assunto
        )

        try:
            canal = await guild.create_text_channel(
                name=nome_do_canal(numero),
                category=categoria,
                overwrites=self.permissoes(guild, interaction.user, cfg),
                topic=f"Ticket #{numero} · {interaction.user} · {assunto}"[:1024],
                reason=f"Ticket #{numero} aberto por {interaction.user}",
            )
        except discord.HTTPException:
            # Sem isto, uma falha na criação deixaria um número queimado e um
            # ticket "aberto" que trava a pessoa de abrir outro.
            await self.bot.db.drop_ticket(ticket_id)
            log.exception("Falha ao criar canal de ticket na guild %s", guild.id)
            raise ZenibotError("Não consegui criar o canal. Avise a equipe.") from None

        await self.bot.db.set_ticket_channel(ticket_id, canal.id)
        ticket = await self.bot.db.get_ticket(ticket_id)

        embed = embeds.info(
            f"**Assunto:** {assunto or '*não informado*'}\n\n"
            "A equipe foi notificada. Descreva o que precisa aqui.",
            title=f"Ticket #{numero}",
        )
        embed.set_footer(text=f"Aberto por {interaction.user}")
        mencoes = " ".join(f"<@&{r}>" for r in cfg.staff_role_ids)
        await canal.send(
            content=f"{interaction.user.mention} {mencoes}".strip(),
            embed=embed,
            view=ticket_view(ticket),
            allowed_mentions=discord.AllowedMentions(
                users=[interaction.user],
                roles=[discord.Object(id=r) for r in cfg.staff_role_ids],
            ),
        )

        await modal.interaction.response.send_message(
            embed=embeds.ok(f"Ticket **#{numero}** aberto em {canal.mention}."),
            ephemeral=True,
        )
        await self.registrar(
            guild,
            f"Ticket **#{numero}** aberto por {interaction.user.mention} "
            f"em {canal.mention}.",
        )

    async def acao_assumir(self, interaction: discord.Interaction, ticket_id: int) -> None:
        if not await eh_staff(interaction):
            raise ZenibotError("Só a equipe pode assumir tickets.")

        ticket = await self.bot.db.get_ticket(ticket_id)
        if ticket is None:
            raise ZenibotError("Esse ticket não existe mais.")

        # Atômico: dois cliques simultâneos não podem ambos assumir.
        if not await self.bot.db.claim_ticket(ticket_id, interaction.user.id):
            atual = (await self.bot.db.get_ticket(ticket_id)).claimed_by
            raise ZenibotError(f"<@{atual}> já assumiu este ticket.")

        atualizado = await self.bot.db.get_ticket(ticket_id)
        await interaction.response.edit_message(view=ticket_view(atualizado))
        await interaction.followup.send(
            embed=embeds.ok(f"{interaction.user.mention} assumiu este ticket."),
            allowed_mentions=embeds.NO_MENTIONS,
        )

    async def acao_fechar(self, interaction: discord.Interaction, ticket_id: int) -> None:
        ticket = await self.bot.db.get_ticket(ticket_id)
        if ticket is None:
            raise ZenibotError("Esse ticket não existe mais.")
        if interaction.user.id != ticket.opener_id and not await eh_staff(interaction):
            raise ZenibotError("Só quem abriu o ticket ou a equipe pode fechá-lo.")

        if not await self.bot.db.close_ticket(ticket_id):
            raise ZenibotError("Este ticket já está fechado.")

        # Fechar remove o acesso de quem abriu, mas mantém o canal: a equipe
        # ainda precisa reler a conversa antes de descartar.
        autor = interaction.guild.get_member(ticket.opener_id)
        if autor is not None:
            try:
                await interaction.channel.set_permissions(
                    autor, overwrite=None, reason=f"Ticket #{ticket.numero} fechado"
                )
            except discord.Forbidden:
                log.warning("Sem permissão para ajustar o canal do ticket %s", ticket_id)

        fechado = await self.bot.db.get_ticket(ticket_id)
        await interaction.response.edit_message(view=ticket_view(fechado))
        await interaction.followup.send(
            embed=embeds.warn(
                f"Ticket fechado por {interaction.user.mention}.\n"
                "O canal continua visível para a equipe até ser apagado."
            ),
            allowed_mentions=embeds.NO_MENTIONS,
        )
        await self.registrar(
            interaction.guild,
            f"Ticket **#{ticket.numero}** fechado por {interaction.user.mention}.",
        )

    async def acao_apagar(self, interaction: discord.Interaction, ticket_id: int) -> None:
        if not await eh_staff(interaction):
            raise ZenibotError("Só a equipe pode apagar o canal.")

        ticket = await self.bot.db.get_ticket(ticket_id)
        if ticket is None:
            raise ZenibotError("Esse ticket não existe mais.")

        await interaction.response.send_message(
            embed=embeds.warn("Apagando o canal..."), ephemeral=True
        )
        # O registro do ticket sobrevive ao canal: o histórico não some junto.
        await self.bot.db.forget_ticket_channel(ticket_id)
        try:
            await interaction.channel.delete(
                reason=f"Ticket #{ticket.numero} apagado por {interaction.user}"
            )
        except discord.Forbidden:
            raise ZenibotError("Não tenho permissão para apagar este canal.") from None

    # ------------------------------------------------------------------

    def permissoes(
        self, guild: discord.Guild, autor: discord.Member, cfg
    ) -> dict:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
            autor: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        for role_id in cfg.staff_role_ids:
            cargo = guild.get_role(role_id)
            if cargo is not None:
                overwrites[cargo] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )
        return overwrites

    async def registrar(self, guild: discord.Guild, texto: str) -> None:
        cfg = await self.bot.db.get_config(guild.id)
        if not cfg.log_channel_id:
            return
        canal = guild.get_channel(cfg.log_channel_id)
        if canal is None:
            return
        try:
            await canal.send(
                embed=embeds.info(texto, title="Tickets"),
                allowed_mentions=embeds.NO_MENTIONS,
            )
        except discord.HTTPException:
            log.warning("Falha ao registrar ticket na guild %s", guild.id)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    ticket = app_commands.Group(
        name="ticket",
        description="Sistema de atendimento por canal privado",
        default_permissions=discord.Permissions(manage_channels=True),
        guild_only=True,
    )

    @ticket.command(name="configurar", description="Define a categoria dos tickets")
    @app_commands.describe(categoria="Onde criar os canais de atendimento")
    @is_staff()
    async def configurar(
        self, interaction: discord.Interaction, categoria: discord.CategoryChannel
    ) -> None:
        if not interaction.guild.me.guild_permissions.manage_channels:
            raise ZenibotError("Preciso da permissão **Gerenciar Canais**.")

        await self.bot.db.set_config(
            interaction.guild_id, ticket_category_id=categoria.id
        )
        cfg = await self.bot.db.get_config(interaction.guild_id)

        aviso = ""
        if not cfg.staff_role_ids:
            aviso = (
                "\n\n⚠️ Nenhum cargo de staff configurado — só quem tem "
                "**Gerenciar Servidor** enxergará os tickets. Defina com "
                "`/config staff`."
            )
        await interaction.response.send_message(
            embed=embeds.ok(
                f"Tickets serão criados em **{categoria.name}**.\n"
                "Publique o painel com `/ticket painel`." + aviso
            ),
            ephemeral=True,
        )

    @ticket.command(name="painel", description="Publica o painel de abertura")
    @app_commands.describe(
        canal="Onde publicar",
        titulo="Título do painel",
        descricao="Texto explicativo",
    )
    @is_staff()
    async def painel(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
        titulo: str = "Precisa de ajuda?",
        descricao: str = "Clique no botão abaixo para falar com a equipe em um canal privado.",
    ) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)
        if not cfg.ticket_category_id:
            raise ZenibotError(
                "Defina a categoria antes: `/ticket configurar categoria:<categoria>`."
            )

        destino = canal or interaction.channel
        perms = destino.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise ZenibotError(
                f"Preciso de **Ver Canal**, **Enviar Mensagens** e **Inserir "
                f"Links** em {destino.mention}."
            )

        await destino.send(
            embed=embeds.info(descricao, title=titulo),
            view=painel_view(),
            allowed_mentions=embeds.NO_MENTIONS,
        )
        await interaction.response.send_message(
            embed=embeds.ok(f"Painel publicado em {destino.mention}."), ephemeral=True
        )

    @ticket.command(name="status", description="Quantos tickets estão abertos")
    @is_staff()
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)
        abertos = await self.bot.db.count_open_tickets(interaction.guild_id)
        embed = embeds.info("", title="Tickets")
        embed.add_field(
            name="Categoria",
            value=f"<#{cfg.ticket_category_id}>" if cfg.ticket_category_id else "*não definida*",
            inline=True,
        )
        embed.add_field(name="Abertos", value=str(abertos), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Tickets(bot))
    bot.add_dynamic_items(TicketButton)
