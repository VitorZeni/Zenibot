"""Configuração da régua de escalonamento automático."""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds, escalation
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.duration import humanize, parse_duration

log = logging.getLogger(__name__)


class Escalation(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    escalonamento = app_commands.Group(
        name="escalonamento",
        description="Punições automáticas por número de infrações",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @escalonamento.command(name="ver", description="Mostra a régua de punições")
    @is_staff()
    async def ver(self, interaction: discord.Interaction) -> None:
        regras = await self.bot.db.get_escalation_rules(interaction.guild_id)
        if not regras:
            await interaction.response.send_message(
                embed=embeds.info(
                    "Nenhuma regra definida — `/aviso` apenas registra a infração.\n\n"
                    "Exemplo de régua:\n"
                    "`/escalonamento definir limite:3 acao:Timeout duracao:1h`\n"
                    "`/escalonamento definir limite:5 acao:Ban duracao:7d`",
                    title="Escalonamento automático",
                ),
                ephemeral=True,
            )
            return

        linhas = [
            f"**{r.threshold}ª infração** → {escalation.describe(r)}" for r in regras
        ]
        embed = embeds.info("\n".join(linhas), title="Régua de escalonamento")
        embed.set_footer(
            text="Conta infrações ativas dos últimos 30 dias. "
            "Punições automáticas não contam."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @escalonamento.command(name="definir", description="Define a punição de um limiar")
    @app_commands.describe(
        limite="Número de infrações que dispara a punição",
        acao="O que fazer ao atingir o limiar",
        duracao="Ex.: 1h, 24h, 7d. Obrigatório para timeout; vazio no ban = permanente",
    )
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="Timeout", value="timeout"),
            app_commands.Choice(name="Expulsar", value="kick"),
            app_commands.Choice(name="Banir", value="ban"),
        ]
    )
    @is_staff()
    async def definir(
        self,
        interaction: discord.Interaction,
        limite: app_commands.Range[int, 1, 100],
        acao: app_commands.Choice[str],
        duracao: str | None = None,
    ) -> None:
        segundos = int(parse_duration(duracao).total_seconds()) if duracao else None
        try:
            escalation.validate(acao.value, segundos)
        except ValueError as exc:
            raise ZenibotError(str(exc)) from exc

        await self.bot.db.set_escalation_rule(
            interaction.guild_id, limite, acao.value, segundos
        )
        detalhe = f" ({humanize(timedelta(seconds=segundos))})" if segundos else ""
        await interaction.response.send_message(
            embed=embeds.ok(
                f"Na **{limite}ª infração**: {acao.name.lower()}{detalhe}."
            ),
            ephemeral=True,
        )

    @escalonamento.command(name="remover", description="Remove a regra de um limiar")
    @app_commands.describe(limite="Limiar a remover")
    @is_staff()
    async def remover(
        self, interaction: discord.Interaction, limite: app_commands.Range[int, 1, 100]
    ) -> None:
        removeu = await self.bot.db.delete_escalation_rule(interaction.guild_id, limite)
        if not removeu:
            raise ZenibotError(f"Não há regra definida para a {limite}ª infração.")
        await interaction.response.send_message(
            embed=embeds.ok(f"Regra da {limite}ª infração removida."), ephemeral=True
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Escalation(bot))
