"""Modelos de mensagem salvos: listar, reutilizar e reaplicar.

O modelo salvo é a fonte da verdade, não a mensagem publicada. Isso é uma
decisão, não uma limitação contornável: componentes V2 não voltam de forma
confiável de uma mensagem para o estado do editor. Reeditar, aqui, significa
reaplicar um modelo sobre a mensagem — o conteúdo vem sempre do banco.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands, ui
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.cogs.builder import BuilderView, payload_para_embed
from zenibot.cogs.containers import (
    ContainerBuilderView,
    montar_container,
    payload_para_blocos,
)
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.messages import resolver_mensagem

log = logging.getLogger(__name__)


class Templates(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    modelo = app_commands.Group(
        name="modelo",
        description="Modelos de mensagem salvos",
        default_permissions=discord.Permissions(manage_messages=True),
        guild_only=True,
    )

    async def autocomplete_nome(
        self, interaction: discord.Interaction, atual: str
    ) -> list[app_commands.Choice[str]]:
        modelos = await self.bot.db.list_templates(interaction.guild_id)
        atual = atual.lower()
        return [
            app_commands.Choice(name=f"{m.nome} ({m.tipo})", value=m.nome)
            for m in modelos
            if atual in m.nome
        ][:25]

    async def buscar(self, guild_id: int, nome: str):
        modelo = await self.bot.db.get_template(guild_id, nome.strip().lower())
        if modelo is None:
            raise ZenibotError(
                f"Não existe modelo chamado `{nome}`. Veja os disponíveis com "
                "`/modelo listar`."
            )
        return modelo

    # ------------------------------------------------------------------

    @modelo.command(name="listar", description="Mostra os modelos salvos")
    @is_staff()
    async def listar(self, interaction: discord.Interaction) -> None:
        modelos = await self.bot.db.list_templates(interaction.guild_id)
        if not modelos:
            await interaction.response.send_message(
                embed=embeds.info(
                    "Nenhum modelo salvo.\n\nMonte uma mensagem com "
                    "`/embed criar` ou `/container criar` e use o botão "
                    "**Salvar modelo**.",
                    title="Modelos",
                ),
                ephemeral=True,
            )
            return

        linhas = [
            f"**{m.nome}** · `{m.tipo}` · por <@{m.criado_por}> · "
            f"{embeds.timestamp(m.updated_at, 'd')}"
            for m in modelos
        ]
        embed = embeds.info("\n".join(linhas)[:4000], title=f"Modelos ({len(modelos)})")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @modelo.command(name="usar", description="Abre um modelo no editor")
    @app_commands.describe(nome="Modelo a carregar", canal="Onde publicar depois")
    @app_commands.autocomplete(nome=autocomplete_nome)
    @is_staff()
    async def usar(
        self,
        interaction: discord.Interaction,
        nome: str,
        canal: discord.TextChannel | None = None,
    ) -> None:
        modelo = await self.buscar(interaction.guild_id, nome)
        destino = canal or interaction.channel
        if not isinstance(destino, discord.TextChannel):
            raise ZenibotError("Escolha um canal de texto como destino.")

        # Abre no editor em vez de publicar direto: quase sempre se quer
        # ajustar uma data ou um nome antes de mandar.
        if modelo.tipo == "embed":
            view = BuilderView(
                interaction.user.id, destino, embed=payload_para_embed(modelo.payload)
            )
            await interaction.response.send_message(
                content=view.status(), embed=view.preview(), view=view, ephemeral=True
            )
        else:
            blocos, cor = payload_para_blocos(modelo.payload)
            view = ContainerBuilderView(
                interaction.user.id, destino, blocos=blocos, cor=cor
            )
            await interaction.response.send_message(view=view, ephemeral=True)

        view.mensagem = await interaction.original_response()

    @modelo.command(
        name="aplicar", description="Reescreve uma mensagem já publicada com um modelo"
    )
    @app_commands.describe(
        nome="Modelo a aplicar",
        mensagem="ID ou link da mensagem publicada por mim",
    )
    @app_commands.autocomplete(nome=autocomplete_nome)
    @is_staff()
    async def aplicar(
        self, interaction: discord.Interaction, nome: str, mensagem: str
    ) -> None:
        modelo = await self.buscar(interaction.guild_id, nome)
        await interaction.response.defer(ephemeral=True)
        alvo = await resolver_mensagem(interaction, mensagem)

        if modelo.tipo == "embed":
            # view=None limpa componentes de uma versão anterior em container.
            await alvo.edit(
                embed=payload_para_embed(modelo.payload),
                view=None,
                allowed_mentions=embeds.NO_MENTIONS,
            )
        else:
            blocos, cor = payload_para_blocos(modelo.payload)
            layout = ui.LayoutView(timeout=None)
            layout.add_item(montar_container(blocos, cor))
            await alvo.edit(
                content=None,
                embeds=[],
                view=layout,
                allowed_mentions=embeds.NO_MENTIONS,
            )

        await interaction.followup.send(
            embed=embeds.ok(f"Mensagem atualizada com o modelo `{modelo.nome}`.\n{alvo.jump_url}"),
            ephemeral=True,
        )

    @modelo.command(name="apagar", description="Remove um modelo salvo")
    @app_commands.describe(nome="Modelo a apagar")
    @app_commands.autocomplete(nome=autocomplete_nome)
    @is_staff()
    async def apagar(self, interaction: discord.Interaction, nome: str) -> None:
        if not await self.bot.db.delete_template(
            interaction.guild_id, nome.strip().lower()
        ):
            raise ZenibotError(f"Não existe modelo chamado `{nome}`.")
        await interaction.response.send_message(
            embed=embeds.ok(f"Modelo `{nome}` apagado."), ephemeral=True
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Templates(bot))
