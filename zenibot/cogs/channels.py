"""Criação de canais privados por cargo.

Substitui o ritual manual: criar o canal, abrir as permissões, negar para
`@everyone`, adicionar cada cargo, marcar as chaves certas em cada um. Aqui
isso vira um comando e um seletor.

Não persiste nada — o estado vive nas permissões do próprio canal, que é onde
o Discord já o guarda. Um registro paralelo só teria como se desatualizar
quando alguém mexesse nas permissões pela interface.
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
from zenibot.core.errors import respond_error
from zenibot.core.guild import MAX_POR_CATEGORIA, categoria_cheia

log = logging.getLogger(__name__)

MAX_CARGOS = 10
TIMEOUT = 300

# Caracteres que o Discord remove de nomes de canal de texto. Normalizar aqui
# faz a prévia bater com o que será criado, em vez de o servidor "corrigir"
# o nome depois e surpreender quem criou.
INVALIDOS = re.compile(r"[^\w\-]", re.UNICODE)
TRACOS = re.compile(r"-{2,}")


def sanitizar_nome(nome: str, *, voz: bool) -> str:
    nome = nome.strip()[:100]
    if voz:
        # Canais de voz aceitam maiúsculas, espaços e pontuação.
        return nome or "sala"
    limpo = INVALIDOS.sub("", nome.lower().replace(" ", "-"))
    limpo = TRACOS.sub("-", limpo).strip("-")
    return limpo[:100] or "canal"


def permissoes_para(
    guild: discord.Guild,
    cargos: list[discord.Role],
    autor: discord.Member,
    *,
    voz: bool,
) -> dict:
    """Monta os overwrites: invisível para todos, visível para os escolhidos."""
    base = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    if voz:
        base.update(connect=True, speak=True)
    else:
        base.update(send_messages=True, attach_files=True)

    # O bot precisa se enxergar para poder administrar o canal depois.
    meu = discord.PermissionOverwrite(view_channel=True, manage_channels=True)
    if voz:
        meu.update(connect=True)

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: meu,
        # Sem isto dá para criar um canal que quem criou não consegue ver.
        autor: base,
    }
    for cargo in cargos:
        overwrites[cargo] = base
    return overwrites


def validar_cargos(cargos: list[discord.Role], guild: discord.Guild) -> None:
    if any(c.is_default() for c in cargos):
        raise ZenibotError(
            "`@everyone` deixaria o canal público — é o oposto do que este "
            "comando faz. Escolha cargos específicos."
        )
    if len(cargos) > MAX_CARGOS:
        raise ZenibotError(f"No máximo {MAX_CARGOS} cargos por canal.")
    if not guild.me.guild_permissions.manage_roles:
        raise ZenibotError(
            "Preciso de **Gerenciar Cargos** para definir quem enxerga o canal."
        )


class CriarView(ui.View):
    """Escolha dos cargos, com prévia antes de criar."""

    def __init__(
        self,
        autor: discord.Member,
        *,
        nome: str,
        voz: bool,
        categoria: discord.CategoryChannel | None,
    ) -> None:
        super().__init__(timeout=TIMEOUT)
        self.autor = autor
        self.nome = nome
        self.voz = voz
        self.categoria = categoria
        self.cargos: list[discord.Role] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor.id:
            await interaction.response.send_message(
                embed=embeds.error("Este painel é de outra pessoa."), ephemeral=True
            )
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: ui.Item, /
    ) -> None:
        await respond_error(interaction, error, contexto="painel de canal")

    def previa(self) -> discord.Embed:
        tipo = "voz" if self.voz else "texto"
        local = f"em **{self.categoria.name}**" if self.categoria else "fora de categoria"
        quem = (
            ", ".join(c.mention for c in self.cargos)
            if self.cargos
            else "*nenhum cargo escolhido ainda*"
        )
        embed = embeds.info(
            f"Canal de {tipo} **{self.nome}**, {local}.\n\n"
            f"**Quem enxerga:** {quem}\n"
            f"Mais você ({self.autor.mention}) e o Zenibot.\n"
            f"**Invisível** para todo o resto do servidor.",
            title="Criar canal privado",
        )
        return embed

    @ui.select(
        cls=ui.RoleSelect,
        placeholder="Cargos que poderão ver o canal",
        min_values=1,
        max_values=MAX_CARGOS,
    )
    async def escolher(
        self, interaction: discord.Interaction, select: ui.RoleSelect
    ) -> None:
        self.cargos = list(select.values)
        self.criar.disabled = False
        await interaction.response.edit_message(embed=self.previa(), view=self)

    @ui.button(label="Criar", style=discord.ButtonStyle.success, disabled=True, row=1)
    async def criar(self, interaction: discord.Interaction, button: ui.Button) -> None:
        guild = interaction.guild
        validar_cargos(self.cargos, guild)

        if categoria_cheia(self.categoria):
            raise ZenibotError(
                f"A categoria **{self.categoria.name}** está cheia "
                f"({MAX_POR_CATEGORIA} canais)."
            )

        overwrites = permissoes_para(guild, self.cargos, self.autor, voz=self.voz)
        criar = guild.create_voice_channel if self.voz else guild.create_text_channel
        try:
            canal = await criar(
                name=self.nome,
                category=self.categoria,
                overwrites=overwrites,
                reason=f"Canal privado criado por {self.autor}",
            )
        except discord.Forbidden:
            raise ZenibotError("Não tenho permissão para criar canais aqui.") from None

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=embeds.ok(
                f"{canal.mention} criado.\n"
                f"Visível para: {', '.join(c.mention for c in self.cargos)}."
            ),
            view=self,
        )
        self.stop()

    @ui.button(label="Cancelar", style=discord.ButtonStyle.danger, row=1)
    async def cancelar(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        await interaction.response.edit_message(
            embed=embeds.info("Cancelado."), view=None
        )
        self.stop()


class Channels(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    canal = app_commands.Group(
        name="canal",
        description="Canais privados por cargo",
        default_permissions=discord.Permissions(manage_channels=True),
        guild_only=True,
    )

    @canal.command(name="criar", description="Cria um canal visível só para certos cargos")
    @app_commands.describe(
        nome="Nome do canal",
        tipo="Texto ou voz",
        categoria="Onde criar (opcional)",
    )
    @app_commands.choices(
        tipo=[
            app_commands.Choice(name="Texto", value="texto"),
            app_commands.Choice(name="Voz", value="voz"),
        ]
    )
    @is_staff()
    async def criar(
        self,
        interaction: discord.Interaction,
        nome: app_commands.Range[str, 1, 100],
        tipo: app_commands.Choice[str],
        categoria: discord.CategoryChannel | None = None,
    ) -> None:
        if not interaction.guild.me.guild_permissions.manage_channels:
            raise ZenibotError("Preciso da permissão **Gerenciar Canais**.")

        voz = tipo.value == "voz"
        limpo = sanitizar_nome(nome, voz=voz)

        view = CriarView(
            interaction.user, nome=limpo, voz=voz, categoria=categoria
        )
        await interaction.response.send_message(
            embed=view.previa(), view=view, ephemeral=True
        )

    @canal.command(
        name="acesso", description="Concede ou remove o acesso de um cargo a um canal"
    )
    @app_commands.describe(canal="Canal a ajustar", cargo="Cargo a alternar")
    @is_staff()
    async def acesso(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | discord.VoiceChannel,
        cargo: discord.Role,
    ) -> None:
        validar_cargos([cargo], interaction.guild)

        atual = canal.overwrites_for(cargo)
        voz = isinstance(canal, discord.VoiceChannel)

        if atual.view_channel:
            # Remover o overwrite inteiro, e não negar: negar deixaria uma
            # regra explícita que atrapalha quem herda acesso da categoria.
            await canal.set_permissions(
                cargo, overwrite=None, reason=f"Acesso removido por {interaction.user}"
            )
            texto = f"{cargo.mention} **não** enxerga mais {canal.mention}."
        else:
            novo = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True
            )
            if voz:
                novo.update(connect=True, speak=True)
            else:
                novo.update(send_messages=True, attach_files=True)
            await canal.set_permissions(
                cargo, overwrite=novo, reason=f"Acesso concedido por {interaction.user}"
            )
            texto = f"{cargo.mention} agora enxerga {canal.mention}."

        await interaction.response.send_message(
            embed=embeds.ok(texto), ephemeral=True
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Channels(bot))
