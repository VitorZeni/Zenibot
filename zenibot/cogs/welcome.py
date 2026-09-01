"""Boas-vindas, autorole e triagem de contas novas.

Depende do intent privilegiado GUILD_MEMBERS. Sem ele habilitado no Developer
Portal, on_member_join simplesmente nunca dispara — e o bot fica em silêncio
sem nenhum erro visível.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.db import now

log = logging.getLogger(__name__)


class Welcome(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        cfg = await self.bot.db.get_config(member.guild.id)
        idade = now() - member.created_at
        conta_nova = (
            cfg.min_account_age_days > 0
            and idade < timedelta(days=cfg.min_account_age_days)
        )

        if conta_nova:
            await self.alert_staff(member, cfg.alert_channel_id, idade)
        else:
            await self.apply_autorole(member, cfg.autorole_id)

        if cfg.welcome_enabled and cfg.welcome_channel_id:
            await self.send_welcome(member, cfg.welcome_channel_id)

    async def alert_staff(
        self, member: discord.Member, channel_id: int | None, idade: timedelta
    ) -> None:
        """Conta nova: sinaliza para a staff e NÃO concede o autorole."""
        log.info(
            "Conta nova em %s: %s (criada há %dd)", member.guild.id, member.id, idade.days
        )
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id)
        if channel is None:
            return

        embed = embeds.warn(
            f"{member.mention} (`{member.id}`) entrou no servidor.\n"
            f"Conta criada {embeds.timestamp(member.created_at, 'R')} "
            f"— **{idade.days} dia(s)**.\n\n"
            "O autorole **não** foi aplicado.",
            title="Conta recém-criada",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed, allowed_mentions=embeds.NO_MENTIONS)
        except discord.HTTPException:
            log.warning("Falha ao alertar staff na guild %s", member.guild.id)

    async def apply_autorole(self, member: discord.Member, role_id: int | None) -> None:
        if not role_id:
            return
        role = member.guild.get_role(role_id)
        if role is None:
            log.warning("Autorole %s não existe mais na guild %s", role_id, member.guild.id)
            return
        # Hierarquia: só consigo atribuir cargos abaixo do meu cargo mais alto.
        if role >= member.guild.me.top_role:
            log.warning("Autorole %s está acima do meu cargo na guild %s",
                        role_id, member.guild.id)
            return

        try:
            await member.add_roles(role, reason="Autorole de entrada")
        except discord.Forbidden:
            log.warning("Sem permissão Manage Roles na guild %s", member.guild.id)

    async def send_welcome(self, member: discord.Member, channel_id: int) -> None:
        channel = member.guild.get_channel(channel_id)
        if channel is None:
            return

        embed = embeds.ok(
            f"{member.mention}, bem-vindo(a) a **{member.guild.name}**!\n"
            f"Você é o membro número **{member.guild.member_count}**.",
            title="Novo membro",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Leia as regras antes de participar.")

        try:
            # allowed_mentions explícito: menciona só quem entrou, nunca @everyone
            # nem cargos, mesmo que apareçam no texto.
            await channel.send(
                content=member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except discord.HTTPException:
            log.warning("Falha ao enviar boas-vindas na guild %s", member.guild.id)


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Welcome(bot))
