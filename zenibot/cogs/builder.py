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
from zenibot.core import colors, embeds
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

# Modelos salvos por servidor. O teto evita que a tabela vire depósito.
LIMITE_MODELOS = 50
LIMITE_NOME_MODELO = 32


def url_valida(valor: str) -> bool:
    return valor.startswith(("http://", "https://"))


def embed_para_payload(embed: discord.Embed) -> dict:
    """Serializa para o modelo salvo. `to_dict` já é o formato da API."""
    return {"embed": embed.to_dict()}


def payload_para_embed(payload: dict) -> discord.Embed:
    return discord.Embed.from_dict(payload["embed"])


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


class CorHexModal(discord.ui.Modal, title="Cor personalizada"):
    """Aberto pela opção "Personalizada" do menu de cores."""

    def __init__(self, atual: discord.Colour | None) -> None:
        super().__init__()
        self.cor = discord.ui.TextInput(
            label="Código da cor",
            required=False,
            placeholder="#5865F2, 5865F2 ou um nome como vermelho",
            max_length=20,
            default=str(atual) if atual else None,
        )
        self.add_item(self.cor)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class ImagensModal(discord.ui.Modal, title="Imagens"):
    def __init__(self, atual: discord.Embed) -> None:
        super().__init__()
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
        for campo in (self.imagem, self.thumbnail):
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


class SalvarModal(discord.ui.Modal, title="Salvar como modelo"):
    nome = discord.ui.TextInput(
        label="Nome do modelo",
        max_length=LIMITE_NOME_MODELO,
        placeholder="regras, boas-vindas, evento-semanal...",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


async def salvar_modelo(
    interaction: discord.Interaction, *, nome: str, tipo: str, payload: dict
) -> None:
    """Grava o modelo e responde. Compartilhado pelos dois construtores."""
    nome = nome.strip().lower()
    if not nome:
        raise ZenibotError("Dê um nome ao modelo.")

    db = interaction.client.db
    if await db.get_template(interaction.guild_id, nome) is None:
        total = await db.count_templates(interaction.guild_id)
        if total >= LIMITE_MODELOS:
            raise ZenibotError(
                f"Este servidor já tem {LIMITE_MODELOS} modelos. "
                "Apague algum com `/modelo apagar`."
            )

    novo = await db.save_template(
        guild_id=interaction.guild_id,
        nome=nome,
        tipo=tipo,
        payload=payload,
        autor_id=interaction.user.id,
    )
    acao = "salvo" if novo else "atualizado"
    await interaction.response.send_message(
        embed=embeds.ok(
            f"Modelo `{nome}` {acao}.\nUse com `/modelo usar nome:{nome}`."
        ),
        ephemeral=True,
    )


class BuilderCorSelect(colors.CorSelect):
    def __init__(self, painel: BuilderView) -> None:
        super().__init__(painel.embed.colour)
        self.painel = painel

    async def aplicar(
        self, interaction: discord.Interaction, cor: discord.Colour | None
    ) -> None:
        self.painel.embed.colour = cor
        await self.painel.refresh(interaction)

    async def pedir_hex(self, interaction: discord.Interaction) -> None:
        modal = CorHexModal(self.painel.embed.colour)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        try:
            self.painel.embed.colour = colors.parse_cor(str(modal.cor))
        except ValueError:
            await modal.interaction.response.send_message(
                embed=embeds.error(
                    f"Cor inválida: `{str(modal.cor).strip()}`.\n"
                    "Use um hex como `#5865F2` ou um nome como `vermelho`."
                ),
                ephemeral=True,
            )
            return
        await self.painel.refresh(modal.interaction)


class BuilderView(discord.ui.View):
    """Painel do rascunho. Só quem abriu consegue operar."""

    def __init__(
        self,
        autor_id: int,
        canal: discord.abc.GuildChannel,
        *,
        embed: discord.Embed | None = None,
    ) -> None:
        super().__init__(timeout=TIMEOUT)
        self.autor_id = autor_id
        self.canal = canal
        self.embed = embed or discord.Embed()
        self.mensagem: discord.InteractionMessage | None = None
        # Menu de cor na linha 1: instância, não decorador, porque precisa
        # nascer refletindo a cor atual do rascunho.
        self.cor_select = BuilderCorSelect(self)
        self.cor_select.row = 1
        self.add_item(self.cor_select)

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

    @discord.ui.button(label="Conteúdo", style=discord.ButtonStyle.primary, row=2)
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

    @discord.ui.button(label="Imagens", style=discord.ButtonStyle.secondary, row=2)
    async def aparencia(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        modal = ImagensModal(self.embed)
        await interaction.response.send_modal(modal)
        if await modal.wait():
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

    @discord.ui.button(label="Rodapé", style=discord.ButtonStyle.secondary, row=2)
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

    @discord.ui.button(label="Publicar", style=discord.ButtonStyle.success, row=3)
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

    @discord.ui.button(label="Salvar modelo", style=discord.ButtonStyle.primary, row=3)
    async def salvar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if embed_vazio(self.embed):
            raise ZenibotError("Nada a salvar — o embed está vazio.")

        modal = SalvarModal()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return

        await salvar_modelo(
            modal.interaction,
            nome=str(modal.nome),
            tipo="embed",
            payload=embed_para_payload(self.embed),
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger, row=3)
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
