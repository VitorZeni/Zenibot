"""Construtor de containers (Components V2).

Um container é o bloco com faixa colorida do Components V2: aceita texto com
markdown, seções com miniatura à direita, separadores e galerias de imagem,
tudo dentro da mesma moldura.

Três restrições da API moldam este código:

1. **Mensagem V2 não aceita `content` nem `embeds`.** Tudo vira componente.
   Por isso a prévia não pode ser um embed: o painel inteiro é um
   `LayoutView`, e o topo dele é literalmente o que será publicado.

2. **Máximo de 40 componentes por mensagem, contando os aninhados.** Os
   controles do painel ocupam 14 desse orçamento enquanto você edita, então
   o limite de blocos aqui é mais apertado que o do resultado publicado.

3. **4000 caracteres somados** em todos os blocos de texto.

O estado é uma lista de blocos e o container é remontado a cada mudança.
Isso mantém desfazer e limpar triviais, e deixa a montagem testável como
função pura, sem Discord nenhum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands, ui
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.cogs.builder import SalvarModal, salvar_modelo
from zenibot.core import colors, embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.errors import respond_error
from zenibot.core.guild import pode_publicar
from zenibot.core.messages import url_valida

log = logging.getLogger(__name__)

LIMITE_COMPONENTES = 40
LIMITE_TEXTO_TOTAL = 4000
LIMITE_TEXTINPUT = 4000
TIMEOUT = 900

# O que os controles consomem do orçamento de 40 na mensagem de prévia:
# status (1) + ações (1+4) + cor (1+1) + edição (1+5) + canal (1+1).
# Um teste confronta esta conta com o payload real, então acrescentar um
# controle aqui quebra o teste em vez de virar 400 em uso.
CUSTO_CONTROLES = 16

# Componentes gerados por bloco. Uma seção conta 3 (ela, o texto e a
# miniatura); uma imagem conta 2 (a galeria e o item dentro dela).
CUSTO_BLOCO = {"texto": 1, "secao": 3, "separador": 1, "imagem": 2}

ROTULOS = {
    "texto": "Texto",
    "secao": "Seção",
    "separador": "Separador",
    "imagem": "Imagem",
}


@dataclass(slots=True)
class Bloco:
    tipo: str
    texto: str = ""
    url: str = ""


def custo_componentes(blocos: list[Bloco]) -> int:
    """Componentes do container publicado, incluindo a própria moldura."""
    return 1 + sum(CUSTO_BLOCO[b.tipo] for b in blocos)


def custo_texto(blocos: list[Bloco]) -> int:
    return sum(len(b.texto) for b in blocos if b.tipo in ("texto", "secao"))


def espaco_disponivel(blocos: list[Bloco]) -> int:
    """Componentes que ainda cabem, já descontados os controles do painel."""
    return LIMITE_COMPONENTES - CUSTO_CONTROLES - custo_componentes(blocos)


def cabe(blocos: list[Bloco], tipo: str) -> bool:
    return espaco_disponivel(blocos) >= CUSTO_BLOCO[tipo]


def montar_container(blocos: list[Bloco], cor: discord.Colour | None) -> ui.Container:
    """Remonta o container a partir da lista de blocos.

    Função pura — é o núcleo testável do construtor.
    """
    container = ui.Container(accent_colour=cor)

    if not blocos:
        container.add_item(
            ui.TextDisplay(
                "### Container vazio\n"
                "Use **+ Texto** para começar. Markdown funciona aqui: "
                "`#` para títulos, `**negrito**`, listas e links."
            )
        )
        return container

    for bloco in blocos:
        if bloco.tipo == "texto":
            container.add_item(ui.TextDisplay(bloco.texto))
        elif bloco.tipo == "separador":
            container.add_item(ui.Separator())
        elif bloco.tipo == "secao":
            secao = ui.Section(accessory=ui.Thumbnail(bloco.url))
            secao.add_item(ui.TextDisplay(bloco.texto))
            container.add_item(secao)
        elif bloco.tipo == "imagem":
            container.add_item(ui.MediaGallery(discord.MediaGalleryItem(bloco.url)))
    return container


def blocos_para_payload(blocos: list[Bloco], cor: discord.Colour | None) -> dict:
    return {
        "cor": cor.value if cor else None,
        "blocos": [
            {"tipo": b.tipo, "texto": b.texto, "url": b.url} for b in blocos
        ],
    }


def payload_para_blocos(
    payload: dict,
) -> tuple[list[Bloco], discord.Colour | None]:
    blocos = [
        Bloco(tipo=b["tipo"], texto=b.get("texto", ""), url=b.get("url", ""))
        for b in payload.get("blocos", [])
        if b.get("tipo") in CUSTO_BLOCO
    ]
    cor = payload.get("cor")
    return blocos, discord.Colour(cor) if cor is not None else None


def resumo(blocos: list[Bloco]) -> str:
    if not blocos:
        return "*nenhum bloco ainda*"
    return " · ".join(f"{i}. {ROTULOS[b.tipo]}" for i, b in enumerate(blocos, 1))


# ---------------------------------------------------------------------------
# Modais
# ---------------------------------------------------------------------------


class TextoModal(ui.Modal, title="Bloco de texto"):
    conteudo = ui.TextInput(
        label="Texto (markdown funciona)",
        style=discord.TextStyle.paragraph,
        max_length=LIMITE_TEXTINPUT,
        placeholder="# Título\nCorpo, **negrito**, listas...",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class SecaoModal(ui.Modal, title="Seção com miniatura"):
    conteudo = ui.TextInput(
        label="Texto da seção",
        style=discord.TextStyle.paragraph,
        max_length=LIMITE_TEXTINPUT,
    )
    imagem = ui.TextInput(label="URL da miniatura", placeholder="https://...")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class ImagemModal(ui.Modal, title="Imagem"):
    imagem = ui.TextInput(label="URL da imagem", placeholder="https://...")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


class CorModal(ui.Modal, title="Cor da faixa"):
    cor = ui.TextInput(
        label="Cor em hex — vazio remove a faixa",
        required=False,
        placeholder="#5865F2",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction


# ---------------------------------------------------------------------------
# Linhas de controle
# ---------------------------------------------------------------------------


class AcoesRow(ui.ActionRow):
    """Adicionar blocos e mudar a cor."""

    def __init__(self, painel: ContainerBuilderView) -> None:
        super().__init__()
        self.painel = painel

    def sync(self) -> None:
        """Desabilita o que não cabe mais no orçamento de componentes."""
        for tipo, botao in (
            ("texto", self.add_texto),
            ("secao", self.add_secao),
            ("separador", self.add_separador),
            ("imagem", self.add_imagem),
        ):
            botao.disabled = not cabe(self.painel.blocos, tipo)

    async def _coletar(
        self, interaction: discord.Interaction, tipo: str
    ) -> None:
        painel = self.painel
        if not cabe(painel.blocos, tipo):
            raise ZenibotError(
                f"Não há espaço para mais um bloco de {ROTULOS[tipo].lower()}: o "
                f"Discord limita a {LIMITE_COMPONENTES} componentes por mensagem, "
                "e os controles deste painel também contam."
            )

        if tipo == "separador":
            painel.blocos.append(Bloco("separador"))
            await painel.refresh(interaction)
            return

        modal = {"texto": TextoModal, "secao": SecaoModal, "imagem": ImagemModal}[tipo]()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return

        if tipo == "texto":
            painel.blocos.append(Bloco("texto", texto=str(modal.conteudo)))
        else:
            url = str(modal.imagem).strip()
            if not url_valida(url):
                await modal.interaction.response.send_message(
                    embed=embeds.error("A URL precisa começar com `https://`."),
                    ephemeral=True,
                )
                return
            if tipo == "secao":
                painel.blocos.append(Bloco("secao", texto=str(modal.conteudo), url=url))
            else:
                painel.blocos.append(Bloco("imagem", url=url))

        await painel.refresh(modal.interaction)

    @ui.button(label="+ Texto", style=discord.ButtonStyle.secondary)
    async def add_texto(self, interaction: discord.Interaction, button: ui.Button):
        await self._coletar(interaction, "texto")

    @ui.button(label="+ Seção", style=discord.ButtonStyle.secondary)
    async def add_secao(self, interaction: discord.Interaction, button: ui.Button):
        await self._coletar(interaction, "secao")

    @ui.button(label="+ Separador", style=discord.ButtonStyle.secondary)
    async def add_separador(self, interaction: discord.Interaction, button: ui.Button):
        await self._coletar(interaction, "separador")

    @ui.button(label="+ Imagem", style=discord.ButtonStyle.secondary)
    async def add_imagem(self, interaction: discord.Interaction, button: ui.Button):
        await self._coletar(interaction, "imagem")

class ContainerCorSelect(colors.CorSelect):
    def __init__(self, painel: ContainerBuilderView) -> None:
        super().__init__(painel.cor)
        self.painel = painel

    async def aplicar(
        self, interaction: discord.Interaction, cor: discord.Colour | None
    ) -> None:
        self.painel.cor = cor
        await self.painel.refresh(interaction)

    async def pedir_hex(self, interaction: discord.Interaction) -> None:
        modal = CorModal()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        try:
            self.painel.cor = colors.parse_cor(str(modal.cor))
        except ValueError:
            await modal.interaction.response.send_message(
                embed=embeds.error(
                    "Cor inválida. Use um hex como `#5865F2` ou um nome como "
                    "`vermelho`."
                ),
                ephemeral=True,
            )
            return
        await self.painel.refresh(modal.interaction)


class CorRow(ui.ActionRow):
    """Um select ocupa a linha inteira, então a cor ganha a sua."""

    def __init__(self, painel: ContainerBuilderView) -> None:
        super().__init__()
        self.painel = painel
        self.add_item(ContainerCorSelect(painel))


class EditarRow(ui.ActionRow):
    """Desfazer, limpar, publicar e cancelar."""

    def __init__(self, painel: ContainerBuilderView) -> None:
        super().__init__()
        self.painel = painel

    def sync(self) -> None:
        vazio = not self.painel.blocos
        self.desfazer.disabled = vazio
        self.limpar.disabled = vazio
        self.publicar.disabled = vazio
        self.salvar.disabled = vazio

    @ui.button(label="Desfazer", style=discord.ButtonStyle.secondary)
    async def desfazer(self, interaction: discord.Interaction, button: ui.Button):
        if self.painel.blocos:
            self.painel.blocos.pop()
        await self.painel.refresh(interaction)

    @ui.button(label="Limpar", style=discord.ButtonStyle.secondary)
    async def limpar(self, interaction: discord.Interaction, button: ui.Button):
        self.painel.blocos.clear()
        await self.painel.refresh(interaction)

    @ui.button(label="Publicar", style=discord.ButtonStyle.success)
    async def publicar(self, interaction: discord.Interaction, button: ui.Button):
        painel = self.painel
        if not painel.blocos:
            raise ZenibotError("O container está vazio — adicione ao menos um bloco.")
        if custo_texto(painel.blocos) > LIMITE_TEXTO_TOTAL:
            raise ZenibotError(
                f"O texto soma {custo_texto(painel.blocos)} caracteres; o limite "
                f"do Discord é {LIMITE_TEXTO_TOTAL}."
            )
        if not painel.pode_publicar():
            raise ZenibotError(
                "Preciso de **Ver Canal**, **Enviar Mensagens** e **Inserir "
                f"Links** em {painel.canal.mention}. Escolha outro canal no "
                "seletor ou ajuste minhas permissões nesse."
            )

        publicado = ui.LayoutView(timeout=None)
        publicado.add_item(montar_container(painel.blocos, painel.cor))
        mensagem = await painel.canal.send(
            view=publicado, allowed_mentions=embeds.NO_MENTIONS
        )

        final = ui.LayoutView(timeout=None)
        final.add_item(montar_container(painel.blocos, painel.cor))
        final.add_item(
            ui.TextDisplay(
                f"-# Publicado em {painel.canal.mention} — {mensagem.jump_url}"
            )
        )
        await interaction.response.edit_message(view=final)
        painel.stop()

    @ui.button(label="Salvar modelo", style=discord.ButtonStyle.primary)
    async def salvar(self, interaction: discord.Interaction, button: ui.Button):
        painel = self.painel
        if not painel.blocos:
            raise ZenibotError("Nada a salvar — o container está vazio.")

        modal = SalvarModal()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return

        await salvar_modelo(
            modal.interaction,
            nome=str(modal.nome),
            tipo="container",
            payload=blocos_para_payload(painel.blocos, painel.cor),
        )

    @ui.button(label="Cancelar", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: ui.Button):
        vazio = ui.LayoutView(timeout=None)
        vazio.add_item(ui.TextDisplay("Rascunho descartado."))
        await interaction.response.edit_message(view=vazio)
        self.painel.stop()


class CanalRow(ui.ActionRow):
    """Um select ocupa a linha inteira — não divide espaço com botões."""

    def __init__(self, painel: ContainerBuilderView) -> None:
        super().__init__()
        self.painel = painel

    @ui.select(
        cls=ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Canal de destino",
    )
    async def canal(
        self, interaction: discord.Interaction, select: ui.ChannelSelect
    ) -> None:
        canal = interaction.guild.get_channel(select.values[0].id)
        if canal is None:
            raise ZenibotError("Não consegui acessar esse canal.")
        self.painel.canal = canal
        await self.painel.refresh(interaction)


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


class ContainerBuilderView(ui.LayoutView):
    """O painel É um LayoutView porque mensagem V2 não comporta embeds.

    A prévia no topo é exatamente o container que será publicado; o status e
    os controles abaixo dela não vão junto.
    """

    def __init__(
        self,
        autor_id: int,
        canal: discord.abc.GuildChannel,
        *,
        blocos: list[Bloco] | None = None,
        cor: discord.Colour | None = None,
    ) -> None:
        super().__init__(timeout=TIMEOUT)
        self.autor_id = autor_id
        self.canal = canal
        self.blocos: list[Bloco] = blocos or []
        self.cor = cor
        self.mensagem: discord.InteractionMessage | None = None

        self.acoes = AcoesRow(self)
        self.cor_row = CorRow(self)
        self.editar = EditarRow(self)
        self.selecao = CanalRow(self)
        self.render()

    def pode_publicar(self) -> bool:
        return pode_publicar(self.canal, self.canal.guild.me)

    def status(self) -> str:
        if not self.pode_publicar():
            return (
                f"Destino: {self.canal.mention} · ⚠️ **não posso publicar aqui** "
                "— faltam permissões nesse canal"
            )
        texto = custo_texto(self.blocos)
        aviso = ""
        if texto > LIMITE_TEXTO_TOTAL:
            aviso = f" · ⚠️ **{texto}/{LIMITE_TEXTO_TOTAL}** caracteres"
        elif texto:
            aviso = f" · {texto}/{LIMITE_TEXTO_TOTAL} caracteres"
        return (
            f"Destino: {self.canal.mention}{aviso}\n"
            f"-# {resumo(self.blocos)} · cabem mais "
            f"{max(espaco_disponivel(self.blocos), 0)} componente(s)"
        )

    def render(self) -> None:
        """Reconstrói o painel a partir do estado atual."""
        self.clear_items()
        self.add_item(montar_container(self.blocos, self.cor))
        self.add_item(ui.TextDisplay(self.status()))
        self.acoes.sync()
        self.editar.sync()
        self.add_item(self.acoes)
        self.add_item(self.cor_row)
        self.add_item(self.editar)
        self.add_item(self.selecao)

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.render()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
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
        item: ui.Item,
        /,
    ) -> None:
        await respond_error(
            interaction,
            error,
            contexto=f"painel de container · {item.__class__.__name__}",
        )

    async def on_timeout(self) -> None:
        if self.mensagem is None:
            return
        expirado = ui.LayoutView(timeout=None)
        expirado.add_item(montar_container(self.blocos, self.cor))
        expirado.add_item(ui.TextDisplay("-# Rascunho expirado."))
        try:
            await self.mensagem.edit(view=expirado)
        except discord.HTTPException:
            pass


class ContainerBuilder(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    container_group = app_commands.Group(
        name="container",
        description="Criação de mensagens com containers (Components V2)",
        default_permissions=discord.Permissions(manage_messages=True),
        guild_only=True,
    )

    @container_group.command(
        name="criar", description="Abre o painel de criação de container"
    )
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

        view = ContainerBuilderView(interaction.user.id, destino)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.mensagem = await interaction.original_response()


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(ContainerBuilder(bot))
