"""Construtor interativo de embeds.

Painel efêmero: você monta por partes, vê a prévia a cada mudança e só então
publica. É o padrão que a Rio Bot usa em `/embed criar`, e ele existe porque
um formulário de um passo só não deixa conferir o resultado antes de mandar
para o canal.

Contraste deliberado com os self-roles: lá os botões usam `DynamicItem` e
sobrevivem a restart, porque o painel fica publicado para sempre. Aqui o
estado é uma `View` comum com timeout — um rascunho que sobrevivesse a um
deploy seria lixo acumulado, não funcionalidade.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.errors import respond_error

log = logging.getLogger(__name__)

# Limites do Discord para embeds. Estourar qualquer um devolve 400 na hora
# de publicar, então validamos antes.
LIMITE_TITULO = 256
LIMITE_DESCRICAO = 4096
LIMITE_RODAPE = 2048
LIMITE_AUTOR = 256
LIMITE_TOTAL = 6000

# Um TextInput de modal aceita no máximo 4000 caracteres — menos que os 4096
# que a descrição de um embed comporta.
LIMITE_TEXTINPUT = 4000

# Tempo de vida do rascunho. Depois disto o painel para de responder.
TIMEOUT = 900


def url_valida(valor: str) -> bool:
    return valor.startswith(("http://", "https://"))


def parse_cor(valor: str) -> discord.Colour | None:
    """Aceita `#5865F2`, `5865F2`, `#58F` (atalho), `0x5865F2` ou `rgb(...)`.

    O `#` é opcional porque quase todo mundo cola o hex sem ele.
    Levanta ValueError no formato inválido.
    """
    valor = valor.strip()
    if not valor:
        return None
    if not valor.startswith(("#", "0x", "rgb")):
        valor = f"#{valor}"

    # O from_str do discord.py completa hex incompleto com zero à esquerda:
    # "#12345" vira "#012345" sem reclamar. Como isso transforma um erro de
    # digitação numa cor errada em silêncio, exigimos 3 ou 6 dígitos.
    if valor.startswith("#") and len(valor) - 1 not in (3, 6):
        raise ValueError(f"hex precisa ter 3 ou 6 dígitos: {valor}")

    return discord.Colour.from_str(valor)


def embed_vazio(embed: discord.Embed) -> bool:
    """Discord recusa um embed sem nenhum conteúdo visível."""
    return not any(
        (
            embed.title,
            embed.description,
            embed.footer.text,
            embed.author.name,
            embed.image.url,
            embed.thumbnail.url,
        )
    )


class ContentModal(discord.ui.Modal, title="Conteúdo do embed"):
    def __init__(self, atual: discord.Embed) -> None:
        super().__init__()
        # Pré-preencher com o valor atual é o que torna a edição incremental:
        # sem isso, cada ajuste exigiria redigitar tudo.
        self.titulo = discord.ui.TextInput(
            label="Título",
            required=False,
            max_length=LIMITE_TITULO,
            default=atual.title or None,
        )
        self.descricao = discord.ui.TextInput(
            label="Descrição",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=LIMITE_TEXTINPUT,
            default=atual.description or None,
        )
        self.url = discord.ui.TextInput(
            label="Link do título (opcional)",
            required=False,
            placeholder="https://...",
            default=atual.url or None,
        )
        for campo in (self.titulo, self.descricao, self.url):
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class AppearanceModal(discord.ui.Modal, title="Aparência"):
    def __init__(self, atual: discord.Embed) -> None:
        super().__init__()
        cor = str(atual.color) if atual.color else None
        self.cor = discord.ui.TextInput(
            label="Cor (hex)",
            required=False,
            placeholder="#5865F2",
            max_length=20,
            default=cor,
        )
        self.imagem = discord.ui.TextInput(
            label="Imagem grande (URL)",
            required=False,
            placeholder="https://...",
            default=atual.image.url or None,
        )
        self.thumbnail = discord.ui.TextInput(
            label="Miniatura (URL)",
            required=False,
            placeholder="https://...",
            default=atual.thumbnail.url or None,
        )
        for campo in (self.cor, self.imagem, self.thumbnail):
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class FooterModal(discord.ui.Modal, title="Rodapé e autor"):
    def __init__(self, atual: discord.Embed) -> None:
        super().__init__()
        self.rodape = discord.ui.TextInput(
            label="Rodapé",
            required=False,
            max_length=LIMITE_RODAPE,
            default=atual.footer.text or None,
        )
        self.autor = discord.ui.TextInput(
            label="Autor",
            required=False,
            max_length=LIMITE_AUTOR,
            default=atual.author.name or None,
        )
        self.autor_icone = discord.ui.TextInput(
            label="Ícone do autor (URL)",
            required=False,
            placeholder="https://...",
            default=atual.author.icon_url or None,
        )
        for campo in (self.rodape, self.autor, self.autor_icone):
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class BuilderView(discord.ui.View):
    """Painel do rascunho. Só quem abriu consegue operar."""

    def __init__(self, autor_id: int, canal: discord.abc.GuildChannel) -> None:
        super().__init__(timeout=TIMEOUT)
        self.autor_id = autor_id
        self.canal = canal
        self.embed = discord.Embed()
        self.mensagem: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """O painel é efêmero, mas a checagem não é redundante: sem ela, um
        rascunho compartilhado por encaminhamento seria operável por outros."""
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                embed=embeds.error("Este rascunho é de outra pessoa."), ephemeral=True
            )
            return False
        return True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
        /,
    ) -> None:
        """Sem isto, a implementação padrão apenas registra no log e o usuário
        recebe "o aplicativo não respondeu a tempo", sem explicação."""
        await respond_error(
            interaction, error, contexto=f"painel de embed · {item.__class__.__name__}"
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.mensagem is not None:
            try:
                await self.mensagem.edit(view=self)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------

    def preview(self) -> discord.Embed:
        """O que mostrar no painel: o rascunho, ou uma dica se estiver vazio."""
        if embed_vazio(self.embed):
            return embeds.info(
                "Comece por **Conteúdo** para dar um título ou texto ao embed.",
                title="Rascunho vazio",
            )
        return self.embed

    def pode_publicar(self) -> bool:
        perms = self.canal.permissions_for(self.canal.guild.me)
        return perms.view_channel and perms.send_messages and perms.embed_links

    def status(self) -> str:
        # Avisar aqui, e não só no Publicar: descobrir que o canal é fechado
        # depois de montar o embed inteiro é o pior momento possível.
        if not self.pode_publicar():
            return (
                f"Destino: {self.canal.mention} · ⚠️ **não posso publicar aqui** "
                "— faltam permissões nesse canal"
            )

        tamanho = len(self.embed)
        aviso = ""
        if tamanho > LIMITE_TOTAL:
            aviso = f" · ⚠️ **{tamanho}/{LIMITE_TOTAL}** caracteres"
        elif tamanho:
            aviso = f" · {tamanho}/{LIMITE_TOTAL} caracteres"
        return f"Destino: {self.canal.mention}{aviso}"

    async def refresh(self, interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=self.status(), embed=self.preview(), view=self
            )
        else:
            await interaction.response.edit_message(
                content=self.status(), embed=self.preview(), view=self
            )

    # ------------------------------------------------------------------

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Canal de destino",
        row=0,
    )
    async def escolher_canal(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ) -> None:
        canal = interaction.guild.get_channel(select.values[0].id)
        if canal is None:
            raise ZenibotError("Não consegui acessar esse canal.")
        self.canal = canal
        await self.refresh(interaction)

    @discord.ui.button(label="Conteúdo", style=discord.ButtonStyle.primary, row=1)
    async def conteudo(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        modal = ContentModal(self.embed)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return  # modal expirou sem envio

        self.embed.title = str(modal.titulo) or None
        self.embed.description = str(modal.descricao) or None

        url = str(modal.url).strip()
        if url and not url_valida(url):
            await modal.interaction.response.send_message(
                embed=embeds.error("O link do título precisa começar com `https://`."),
                ephemeral=True,
            )
            return
        self.embed.url = url or None

        await self.refresh(modal.interaction)

    @discord.ui.button(label="Aparência", style=discord.ButtonStyle.secondary, row=1)
    async def aparencia(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        modal = AppearanceModal(self.embed)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return

        try:
            self.embed.colour = parse_cor(str(modal.cor))
        except ValueError:
            await modal.interaction.response.send_message(
                embed=embeds.error(
                    f"Cor inválida: `{str(modal.cor).strip()}`. "
                    "Use um hex como `#5865F2`."
                ),
                ephemeral=True,
            )
            return

        for valor, rotulo, aplicar in (
            (str(modal.imagem).strip(), "imagem", self.embed.set_image),
            (str(modal.thumbnail).strip(), "miniatura", self.embed.set_thumbnail),
        ):
            if valor and not url_valida(valor):
                await modal.interaction.response.send_message(
                    embed=embeds.error(
                        f"A URL da {rotulo} precisa começar com `https://`."
                    ),
                    ephemeral=True,
                )
                return
            aplicar(url=valor or None)

        await self.refresh(modal.interaction)

    @discord.ui.button(label="Rodapé", style=discord.ButtonStyle.secondary, row=1)
    async def rodape(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        modal = FooterModal(self.embed)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return

        texto = str(modal.rodape).strip()
        self.embed.set_footer(text=texto or None)

        autor = str(modal.autor).strip()
        icone = str(modal.autor_icone).strip()
        if icone and not url_valida(icone):
            await modal.interaction.response.send_message(
                embed=embeds.error("O ícone do autor precisa começar com `https://`."),
                ephemeral=True,
            )
            return
        if autor:
            self.embed.set_author(name=autor, icon_url=icone or None)
        else:
            self.embed.remove_author()

        await self.refresh(modal.interaction)

    @discord.ui.button(label="Publicar", style=discord.ButtonStyle.success, row=2)
    async def publicar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if embed_vazio(self.embed):
            raise ZenibotError("O embed está vazio — adicione ao menos um título ou texto.")
        if len(self.embed) > LIMITE_TOTAL:
            raise ZenibotError(
                f"O embed tem {len(self.embed)} caracteres; o limite do Discord "
                f"é {LIMITE_TOTAL}."
            )

        if not self.pode_publicar():
            raise ZenibotError(
                f"Preciso de **Ver Canal**, **Enviar Mensagens** e **Inserir "
                f"Links** em {self.canal.mention}. Escolha outro canal no "
                "seletor ou ajuste minhas permissões nesse."
            )

        # NO_MENTIONS não é detalhe: sem ele, um anúncio com @everyone no texto
        # vira notificação para o servidor inteiro.
        mensagem = await self.canal.send(
            embed=self.embed, allowed_mentions=embeds.NO_MENTIONS
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Publicado em {self.canal.mention} — {mensagem.jump_url}",
            embed=self.embed,
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger, row=2)
    async def cancelar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="Rascunho descartado.", embed=None, view=None
        )
        self.stop()


class EmbedBuilder(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    embed_group = app_commands.Group(
        name="embed",
        description="Criação de mensagens em embed",
        default_permissions=discord.Permissions(manage_messages=True),
        guild_only=True,
    )

    @embed_group.command(name="criar", description="Abre o painel de criação de embed")
    @app_commands.describe(canal="Onde publicar. Padrão: este canal")
    @is_staff()
    async def criar(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
    ) -> None:
        destino = canal or interaction.channel
        if not isinstance(destino, discord.TextChannel):
            raise ZenibotError("Escolha um canal de texto como destino.")

        view = BuilderView(interaction.user.id, destino)
        await interaction.response.send_message(
            content=view.status(), embed=view.preview(), view=view, ephemeral=True
        )
        view.mensagem = await interaction.original_response()


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(EmbedBuilder(bot))
