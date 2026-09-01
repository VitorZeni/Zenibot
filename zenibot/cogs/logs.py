"""Logs de auditoria em tempo real.

Usa o evento de Gateway GUILD_AUDIT_LOG_ENTRY_CREATE (intent GUILD_MODERATION
+ permissão View Audit Log). Isso captura inclusive ações feitas por humanos
direto no cliente do Discord — e evita o anti-padrão de fazer polling do
endpoint REST de audit log, que só desperdiça rate limit.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds

log = logging.getLogger(__name__)

# Só registramos ações administrativas relevantes. Logar tudo transforma o
# canal em ruído e queima rate limit em servidores movimentados.
WATCHED: dict[discord.AuditLogAction, str] = {
    discord.AuditLogAction.kick: "Membro expulso",
    discord.AuditLogAction.ban: "Membro banido",
    discord.AuditLogAction.unban: "Membro desbanido",
    discord.AuditLogAction.member_update: "Membro atualizado",
    discord.AuditLogAction.member_role_update: "Cargos alterados",
    discord.AuditLogAction.channel_create: "Canal criado",
    discord.AuditLogAction.channel_delete: "Canal excluído",
    discord.AuditLogAction.channel_update: "Canal atualizado",
    discord.AuditLogAction.role_create: "Cargo criado",
    discord.AuditLogAction.role_delete: "Cargo excluído",
    discord.AuditLogAction.role_update: "Cargo atualizado",
    discord.AuditLogAction.webhook_create: "Webhook criado",
    discord.AuditLogAction.webhook_delete: "Webhook excluído",
    discord.AuditLogAction.message_delete: "Mensagens apagadas por moderador",
    discord.AuditLogAction.message_bulk_delete: "Purge de mensagens",
    discord.AuditLogAction.automod_block_message: "AutoMod bloqueou mensagem",
    discord.AuditLogAction.automod_timeout_member: "AutoMod aplicou timeout",
}


class Logs(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    async def log_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        cfg = await self.bot.db.get_config(guild.id)
        if not cfg.log_channel_id:
            return None
        return guild.get_channel(cfg.log_channel_id)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        titulo = WATCHED.get(entry.action)
        if titulo is None:
            return
        # Ações do próprio bot já foram registradas como casos pelo cog de
        # moderação; logar de novo duplicaria a entrada.
        if entry.user_id == self.bot.user.id:
            return

        channel = await self.log_channel(entry.guild)
        if channel is None:
            return

        embed = embeds.info("", title=titulo)
        if entry.user is not None:
            embed.add_field(name="Autor", value=f"{entry.user.mention}\n`{entry.user.id}`")
        if entry.target is not None:
            alvo = getattr(entry.target, "mention", None) or str(entry.target)
            embed.add_field(name="Alvo", value=f"{alvo}\n`{entry.target_id}`")
        if entry.reason:
            embed.add_field(name="Motivo", value=entry.reason[:1024], inline=False)

        mudancas = self.format_changes(entry)
        if mudancas:
            embed.add_field(name="Mudanças", value=mudancas[:1024], inline=False)

        embed.set_footer(text=f"Entrada de audit log {entry.id}")
        embed.timestamp = entry.created_at

        try:
            await channel.send(embed=embed, allowed_mentions=embeds.NO_MENTIONS)
        except discord.HTTPException:
            log.warning("Falha ao escrever log na guild %s", entry.guild.id)

    @staticmethod
    def format_changes(entry: discord.AuditLogEntry) -> str:
        linhas: list[str] = []
        for change in entry.changes:
            antes = getattr(change, "before", None)
            depois = getattr(change, "after", None)
            atributo = getattr(change, "attribute", "?")
            linhas.append(f"`{atributo}`: {antes!s:.60} → {depois!s:.60}")
        return "\n".join(linhas[:8])

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction) -> None:
        """Acionamentos do AutoMod nativo (intent AUTO_MODERATION_EXECUTION).

        O AutoMod roda server-side, antes da mensagem existir — por isso ele
        cobre filtragem de conteúdo sem exigir o intent MESSAGE_CONTENT.
        """
        guild = self.bot.get_guild(execution.guild_id)
        if guild is None:
            return
        channel = await self.log_channel(guild)
        if channel is None:
            return

        embed = embeds.warn(
            f"<@{execution.user_id}> (`{execution.user_id}`) acionou uma regra do AutoMod.",
            title="AutoMod acionado",
        )
        embed.add_field(name="Ação", value=str(execution.action.type.name), inline=True)
        if execution.matched_keyword:
            embed.add_field(name="Termo", value=f"`{execution.matched_keyword}`", inline=True)
        if execution.channel_id:
            embed.add_field(name="Canal", value=f"<#{execution.channel_id}>", inline=True)

        try:
            await channel.send(embed=embed, allowed_mentions=embeds.NO_MENTIONS)
        except discord.HTTPException:
            log.warning("Falha ao logar AutoMod na guild %s", guild.id)


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Logs(bot))
