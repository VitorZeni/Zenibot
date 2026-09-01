"""Moderação com histórico de casos persistente.

Toda ação: (1) valida hierarquia, (2) tenta avisar o alvo por DM antes de
agir, (3) executa com `reason=` — que vira o header X-Audit-Log-Reason e
torna a ação rastreável no audit log nativo do Discord —, (4) registra o caso
no banco, (5) espelha no canal de logs.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError, assert_can_moderate, is_staff
from zenibot.core.db import now
from zenibot.core.duration import MAX_TIMEOUT, humanize, parse_duration

log = logging.getLogger(__name__)


class Moderation(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    async def notify_target(
        self, member: discord.abc.User, guild: discord.Guild, action: str, reason: str
    ) -> bool:
        """Avisa o alvo por DM. Falha silenciosa é esperada (DMs fechadas)."""
        try:
            await member.send(
                embed=embeds.warn(
                    f"**Servidor:** {guild.name}\n**Motivo:** {reason}",
                    title=f"Você recebeu: {action}",
                )
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def record(
        self,
        interaction: discord.Interaction,
        *,
        target: discord.abc.User,
        action: str,
        reason: str,
        duration: timedelta | None = None,
    ) -> int:
        """Persiste o caso e espelha no canal de logs. Devolve o nº do caso."""
        case_number = await self.bot.db.add_case(
            guild_id=interaction.guild_id,
            user_id=target.id,
            moderator_id=interaction.user.id,
            action=action,
            reason=reason,
            duration_s=int(duration.total_seconds()) if duration else None,
        )

        embed = embeds.case_embed(
            case_number=case_number,
            action=action,
            target=target,
            moderator=interaction.user,
            reason=reason,
            duration=humanize(duration) if duration else None,
        )

        cfg = await self.bot.db.get_config(interaction.guild_id)
        if cfg.log_channel_id:
            channel = interaction.guild.get_channel(cfg.log_channel_id)
            if channel is not None:
                try:
                    await channel.send(embed=embed, allowed_mentions=embeds.NO_MENTIONS)
                except discord.HTTPException:
                    log.warning("Falha ao escrever no canal de logs da guild %s",
                                interaction.guild_id)
        return case_number

    # ------------------------------------------------------------------

    @app_commands.command(name="aviso", description="Registra um aviso formal")
    @app_commands.describe(membro="Quem avisar", motivo="Razão do aviso")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def warn(
        self, interaction: discord.Interaction, membro: discord.Member, motivo: str
    ) -> None:
        assert_can_moderate(interaction.user, membro, interaction.guild.me)
        await interaction.response.defer(ephemeral=True)

        await self.notify_target(membro, interaction.guild, "Aviso", motivo)
        case = await self.record(interaction, target=membro, action="warn", reason=motivo)

        total = await self.bot.db.count_active_cases(interaction.guild_id, membro.id)
        await interaction.followup.send(
            embed=embeds.ok(
                f"Aviso registrado como caso **#{case}**.\n"
                f"{membro.mention} tem **{total}** infração(ões) nos últimos 30 dias."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="silenciar", description="Timeout temporário (máx. 28 dias)")
    @app_commands.describe(
        membro="Quem silenciar",
        duracao="Ex.: 30m, 2h, 1h30m, 7d",
        motivo="Razão do timeout",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def timeout(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        duracao: str,
        motivo: str = "Sem motivo informado",
    ) -> None:
        assert_can_moderate(interaction.user, membro, interaction.guild.me)
        delta = parse_duration(duracao)
        if delta > MAX_TIMEOUT:
            raise ZenibotError("O Discord limita o timeout a 28 dias. Use `/banir` acima disso.")

        await interaction.response.defer(ephemeral=True)
        await self.notify_target(membro, interaction.guild, f"Timeout ({humanize(delta)})", motivo)
        # reason= vira o header X-Audit-Log-Reason.
        await membro.timeout(delta, reason=f"{interaction.user} — {motivo}")

        case = await self.record(
            interaction, target=membro, action="timeout", reason=motivo, duration=delta
        )
        await interaction.followup.send(
            embed=embeds.ok(
                f"{membro.mention} silenciado por **{humanize(delta)}** · caso **#{case}**.\n"
                f"Expira {embeds.timestamp(now() + delta, 'R')}."
            ),
            ephemeral=True,
        )

    @app_commands.command(name="expulsar", description="Remove o membro do servidor")
    @app_commands.describe(membro="Quem expulsar", motivo="Razão")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def kick(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Sem motivo informado",
    ) -> None:
        assert_can_moderate(interaction.user, membro, interaction.guild.me)
        await interaction.response.defer(ephemeral=True)

        # A DM precisa sair ANTES do kick: depois não há canal mútuo.
        await self.notify_target(membro, interaction.guild, "Expulsão", motivo)
        await membro.kick(reason=f"{interaction.user} — {motivo}")

        case = await self.record(interaction, target=membro, action="kick", reason=motivo)
        await interaction.followup.send(
            embed=embeds.ok(f"{membro} expulso · caso **#{case}**."), ephemeral=True
        )

    @app_commands.command(name="banir", description="Bane um usuário, com duração opcional")
    @app_commands.describe(
        usuario="Quem banir (aceita ID de quem já saiu)",
        motivo="Razão",
        duracao="Ex.: 7d. Vazio = permanente",
        apagar_dias="Apagar mensagens dos últimos N dias (0-7)",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def ban(
        self,
        interaction: discord.Interaction,
        usuario: discord.User,
        motivo: str = "Sem motivo informado",
        duracao: str | None = None,
        apagar_dias: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        # discord.User cobre quem já saiu do servidor; só validamos hierarquia
        # quando o alvo ainda é membro.
        membro = interaction.guild.get_member(usuario.id)
        if membro is not None:
            assert_can_moderate(interaction.user, membro, interaction.guild.me)

        delta = parse_duration(duracao) if duracao else None
        await interaction.response.defer(ephemeral=True)

        if membro is not None:
            rotulo = f"Banimento ({humanize(delta)})" if delta else "Banimento permanente"
            await self.notify_target(membro, interaction.guild, rotulo, motivo)

        await interaction.guild.ban(
            usuario,
            reason=f"{interaction.user} — {motivo}",
            delete_message_seconds=apagar_dias * 86400,
        )

        case = await self.record(
            interaction, target=usuario, action="ban", reason=motivo, duration=delta
        )

        extra = ""
        if delta:
            # Agendamento persistente: sobrevive a restart, ao contrário de
            # um asyncio.sleep().
            await self.bot.db.schedule(
                guild_id=interaction.guild_id,
                user_id=usuario.id,
                kind="unban",
                run_at=now() + delta,
                payload={"case": case},
            )
            extra = f"\nDesbanimento automático {embeds.timestamp(now() + delta, 'R')}."

        await interaction.followup.send(
            embed=embeds.ok(f"{usuario} banido · caso **#{case}**.{extra}"), ephemeral=True
        )

    @app_commands.command(name="desbanir", description="Remove o banimento de um usuário")
    @app_commands.describe(usuario="ID do usuário banido", motivo="Razão")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def unban(
        self,
        interaction: discord.Interaction,
        usuario: discord.User,
        motivo: str = "Sem motivo informado",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.guild.unban(usuario, reason=f"{interaction.user} — {motivo}")
        except discord.NotFound:
            raise ZenibotError("Esse usuário não está banido.") from None

        # Cancela o unban agendado, se havia um banimento temporário.
        await self.bot.db.cancel_jobs(
            guild_id=interaction.guild_id, user_id=usuario.id, kind="unban"
        )
        case = await self.record(interaction, target=usuario, action="unban", reason=motivo)
        await interaction.followup.send(
            embed=embeds.ok(f"{usuario} desbanido · caso **#{case}**."), ephemeral=True
        )

    # ------------------------------------------------------------------
    # Consulta de histórico
    # ------------------------------------------------------------------

    @app_commands.command(name="caso", description="Detalha um caso pelo número")
    @app_commands.describe(numero="Número do caso neste servidor")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def case(self, interaction: discord.Interaction, numero: int) -> None:
        registro = await self.bot.db.get_case(interaction.guild_id, numero)
        if registro is None:
            raise ZenibotError(f"Caso #{numero} não existe neste servidor.")

        alvo = await self.bot.fetch_user(registro.user_id)
        moderador = await self.bot.fetch_user(registro.moderator_id)
        embed = embeds.case_embed(
            case_number=registro.case_number,
            action=registro.action,
            target=alvo,
            moderator=moderador,
            reason=registro.reason,
            duration=(
                humanize(timedelta(seconds=registro.duration_s))
                if registro.duration_s
                else None
            ),
        )
        embed.add_field(
            name="Quando", value=embeds.timestamp(registro.created_at), inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="historico", description="Histórico de moderação de um usuário")
    @app_commands.describe(usuario="Quem consultar")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    @is_staff()
    async def history(self, interaction: discord.Interaction, usuario: discord.User) -> None:
        casos = await self.bot.db.get_user_cases(interaction.guild_id, usuario.id, limit=15)
        if not casos:
            await interaction.response.send_message(
                embed=embeds.info(f"{usuario.mention} não tem registros neste servidor."),
                ephemeral=True,
            )
            return

        linhas = [
            f"**#{c.case_number}** · `{c.action}` · {embeds.timestamp(c.created_at, 'd')}\n"
            f"　{c.reason[:120]}"
            for c in casos
        ]
        embed = embeds.info("\n".join(linhas), title=f"Histórico · {usuario}")
        embed.set_footer(text=f"{len(casos)} caso(s) mais recente(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Moderation(bot))
