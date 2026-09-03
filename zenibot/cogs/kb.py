"""Consulta à base de conhecimento.

O bot **recorta**, não redige. Cada resposta é o bloco de resumo que já está
escrito no arquivo, mais a atribuição ao autor e os avisos obrigatórios. Não
há geração de texto, então não há como inventar um número — e num acervo cujo
valor inteiro está na precisão dos números, isso é o requisito, não um
detalhe.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds, kb
from zenibot.core.checks import ZenibotError, is_owner

log = logging.getLogger(__name__)

LIMITE_DESCRICAO = 4000
LIMITE_CAMPO = 1024

# Avisos que acompanham toda publicação deste acervo.
#
# Fonte: README.md e INDICE.md da pasta AION2, seção "Três avisos que o bot
# deve carregar em toda resposta". Se a fonte for atualizada — e ela é
# work-in-progress, com mudança esperada no launch Global — este texto
# precisa acompanhar. Há teste garantindo que os três continuam presentes.
AVISOS = (
    "• Os números são de **KR**, não do Global: o Global lança com menos "
    "Arcanas e sets, e o valor percentual de cada stat muda.\n"
    "• **Front/Back Attack Damage Boost** deve ser ajustado antes do Global.\n"
    "• **Ping altera tudo** — 150–200 ms equivaleram a 50–70% do dano nos "
    "testes do autor."
)


def corta(texto: str, limite: int) -> str:
    """Corta preservando linha inteira, para não partir uma tabela ao meio."""
    if len(texto) <= limite:
        return texto
    recorte = texto[: limite - 40]
    if "\n" in recorte:
        recorte = recorte[: recorte.rindex("\n")]
    return f"{recorte}\n\n*(…continua no acervo)*"


class Conhecimento(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot
        self.acervo: list[kb.Documento] = []
        self.recarregar()

    def recarregar(self) -> int:
        diretorio = self.bot.settings.kb_dir
        self.acervo = kb.carregar_acervo(diretorio)
        if diretorio is None:
            log.info("Base de conhecimento não configurada (ZENIBOT_KB_PATH)")
        else:
            log.info("Acervo carregado: %d capítulo(s) de %s", len(self.acervo), diretorio)
        return len(self.acervo)

    def exigir_acervo(self) -> None:
        if not self.acervo:
            raise ZenibotError(
                "A base de conhecimento não está configurada neste bot. "
                "Defina `ZENIBOT_KB_PATH` apontando para a pasta dos capítulos."
            )

    def por_slug(self, slug: str) -> kb.Documento:
        for doc in self.acervo:
            if doc.slug == slug:
                return doc
        raise ZenibotError(
            f"Não encontrei `{slug}` no acervo. Veja a lista com `/aion indice`."
        )

    def montar(self, doc: kb.Documento) -> discord.Embed:
        embed = embeds.info(
            corta(kb.resumo_publicavel(doc.resumo), LIMITE_DESCRICAO),
            title=doc.titulo or doc.slug,
        )
        embed.add_field(name="⚠️ Antes de usar estes números", value=AVISOS, inline=False)

        credito = doc.autor or "autor não informado"
        if doc.fonte:
            credito = f"{doc.fonte} — {credito}"
        if doc.atualizado_em:
            credito += f" · atualizado em {doc.atualizado_em}"
        embed.set_footer(text=corta(credito, 2048))
        return embed

    # ------------------------------------------------------------------

    aion = app_commands.Group(
        name="aion",
        description="Consulta à base de conhecimento de Aion 2",
        guild_only=True,
    )

    async def autocomplete_capitulo(
        self, interaction: discord.Interaction, atual: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=doc.rotulo, value=doc.slug)
            for doc in kb.buscar(self.acervo, atual)
        ]

    @aion.command(name="capitulo", description="Mostra o resumo de um capítulo")
    @app_commands.describe(
        capitulo="Qual capítulo consultar",
        privado="Só você vê a resposta",
    )
    @app_commands.autocomplete(capitulo=autocomplete_capitulo)
    @app_commands.checks.cooldown(4, 60.0)
    async def capitulo(
        self,
        interaction: discord.Interaction,
        capitulo: str,
        privado: bool = False,
    ) -> None:
        self.exigir_acervo()
        doc = self.por_slug(capitulo)
        await interaction.response.send_message(
            embed=self.montar(doc),
            ephemeral=privado,
            allowed_mentions=embeds.NO_MENTIONS,
        )

    @aion.command(name="buscar", description="Procura capítulos por termo ou tag")
    @app_commands.describe(termo="Palavra, tag ou trecho do resumo")
    @app_commands.checks.cooldown(4, 60.0)
    async def buscar(self, interaction: discord.Interaction, termo: str) -> None:
        self.exigir_acervo()
        achados = kb.buscar(self.acervo, termo, limite=10)
        if not achados:
            raise ZenibotError(
                f"Nada no acervo menciona `{termo}`. Veja o que existe com "
                "`/aion indice`."
            )

        linhas = [
            f"**{doc.titulo or doc.slug}**\n"
            f"　`/aion capitulo:{doc.slug}`"
            + (f" · {', '.join(doc.tags[:5])}" if doc.tags else "")
            for doc in achados
        ]
        embed = embeds.info(
            corta("\n\n".join(linhas), LIMITE_DESCRICAO),
            title=f"{len(achados)} capítulo(s) para “{termo}”",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @aion.command(name="indice", description="Lista os capítulos disponíveis")
    async def indice(self, interaction: discord.Interaction) -> None:
        self.exigir_acervo()
        linhas = [f"`{doc.slug}` — {doc.titulo}" for doc in self.acervo]
        embed = embeds.info(
            corta("\n".join(linhas), LIMITE_DESCRICAO),
            title=f"Acervo · {len(self.acervo)} capítulo(s)",
        )
        primeiro = self.acervo[0]
        if primeiro.fonte:
            embed.set_footer(text=f"{primeiro.fonte} — {primeiro.autor}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @aion.command(name="recarregar", description="[dono] Relê o acervo do disco")
    @is_owner()
    async def recarregar_cmd(self, interaction: discord.Interaction) -> None:
        total = self.recarregar()
        await interaction.response.send_message(
            embed=embeds.ok(f"{total} capítulo(s) carregado(s)."), ephemeral=True
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Conhecimento(bot))
