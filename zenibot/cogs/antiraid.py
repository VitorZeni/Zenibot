"""Anti-raid: detecta picos de entrada e reage."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import antiraid, embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.db import now
from zenibot.core.duration import humanize, parse_duration

log = logging.getLogger(__name__)

# Contas mais novas que isto são destacadas no alerta: raids quase sempre
# usam contas recém-criadas em lote.
CONTA_NOVA = timedelta(days=7)


class AntiRaid(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot
        self.window = antiraid.JoinWindow()
        # Depois de disparar, silencia por um tempo: sem isto, cada entrada
        # seguinte do mesmo raid geraria um alerta novo.
        self._silence_until: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        cfg = await self.bot.db.get_config(member.guild.id)
        if cfg.raid_joins <= 0:
            return

        agora = time.monotonic()
        self.window.record(member.guild.id, member.id, agora)
        recentes = self.window.recent(member.guild.id, cfg.raid_window_s, agora)

        if len(recentes) < cfg.raid_joins:
            return
        if agora < self._silence_until.get(member.guild.id, 0.0):
            return

        self._silence_until[member.guild.id] = agora + cfg.raid_window_s * 2
        await self.trigger(member.guild, cfg, recentes)

    async def trigger(self, guild: discord.Guild, cfg, ids: list[int]) -> None:
        log.warning(
            "Possível raid na guild %s: %d entradas em %ds",
            guild.id,
            len(ids),
            cfg.raid_window_s,
        )

        bloqueio = None
        if cfg.raid_action == "lockdown":
            bloqueio = await self.lockdown(guild, cfg)

        await self.alert(guild, cfg, ids, bloqueio)

    async def lockdown(self, guild: discord.Guild, cfg) -> str | None:
        anterior = antiraid.snapshot(guild)
        ok = await antiraid.apply_lockdown(guild, "Anti-raid: pico de entradas")
        if not ok:
            return None

        # O estado anterior vai no job persistente, não em memória: um
        # restart durante o bloqueio não pode deixar o servidor trancado.
        await self.bot.db.schedule(
            guild_id=guild.id,
            user_id=self.bot.user.id,
            kind="raid_end",
            run_at=now() + timedelta(minutes=cfg.raid_lockdown_minutes),
            payload=anterior,
        )
        return (
            f"Convites pausados e verificação elevada por "
            f"**{cfg.raid_lockdown_minutes} min**."
        )

    async def alert(
        self, guild: discord.Guild, cfg, ids: list[int], bloqueio: str | None
    ) -> None:
        canal_id = cfg.alert_channel_id or cfg.log_channel_id
        canal = guild.get_channel(canal_id) if canal_id else None
        if canal is None:
            log.warning("Raid na guild %s sem canal de alertas configurado", guild.id)
            return

        membros = [m for m in (guild.get_member(i) for i in ids) if m is not None]
        novas = sum(1 for m in membros if now() - m.created_at < CONTA_NOVA)

        embed = embeds.error(
            f"**{len(ids)}** entradas em **{cfg.raid_window_s}s** "
            f"(limiar: {cfg.raid_joins}).\n"
            f"**{novas}** com conta criada há menos de 7 dias.",
            title="Possível raid detectado",
        )
        amostra = "\n".join(
            f"{m.mention} `{m.id}` — conta {embeds.timestamp(m.created_at, 'R')}"
            for m in membros[:10]
        )
        if amostra:
            embed.add_field(
                name=f"Entradas ({min(len(membros), 10)} de {len(ids)})",
                value=amostra[:1024],
                inline=False,
            )
        embed.add_field(
            name="Ação automática",
            value=bloqueio
            or (
                "Nenhuma — apenas alerta."
                if cfg.raid_action == "alert"
                else "⚠️ Bloqueio falhou: preciso da permissão **Gerenciar Servidor**."
            ),
            inline=False,
        )
        embed.set_footer(text="/antiraid liberar remove o bloqueio antes da hora")
        embed.timestamp = now()

        # Alerta de raid que não notifica ninguém é inútil: aqui a menção aos
        # cargos de staff é intencional e explicitamente permitida.
        mencao = " ".join(f"<@&{r}>" for r in cfg.staff_role_ids)
        try:
            await canal.send(
                content=mencao or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(id=r) for r in cfg.staff_role_ids]
                ),
            )
        except discord.HTTPException:
            log.warning("Falha ao alertar raid na guild %s", guild.id)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    antiraid_group = app_commands.Group(
        name="antiraid",
        description="Detecção de picos de entrada",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @antiraid_group.command(name="ver", description="Mostra a configuração anti-raid")
    @is_staff()
    async def ver(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)
        if cfg.raid_joins <= 0:
            await interaction.response.send_message(
                embed=embeds.info(
                    "Anti-raid desativado.\n\n"
                    "Exemplo: `/antiraid configurar entradas:5 janela:30s acao:Alertar`",
                    title="Anti-raid",
                ),
                ephemeral=True,
            )
            return

        pendente = await self.bot.db.get_pending_job(interaction.guild_id, "raid_end")
        embed = embeds.info("", title="Anti-raid")
        embed.add_field(
            name="Gatilho",
            value=f"{cfg.raid_joins} entradas em {cfg.raid_window_s}s",
            inline=True,
        )
        embed.add_field(
            name="Ação",
            value="Bloquear servidor" if cfg.raid_action == "lockdown" else "Só alertar",
            inline=True,
        )
        if cfg.raid_action == "lockdown":
            embed.add_field(
                name="Duração do bloqueio",
                value=f"{cfg.raid_lockdown_minutes} min",
                inline=True,
            )
            if not interaction.guild.me.guild_permissions.manage_guild:
                embed.add_field(
                    name="⚠️ Atenção",
                    value=(
                        "Falta a permissão **Gerenciar Servidor** — o bloqueio "
                        "vai falhar e só o alerta será enviado."
                    ),
                    inline=False,
                )
        if pendente:
            embed.add_field(
                name="Bloqueio ativo",
                value=f"Termina {embeds.timestamp(pendente.run_at, 'R')}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @antiraid_group.command(name="configurar", description="Define o gatilho e a reação")
    @app_commands.describe(
        entradas="Número de entradas que dispara o alerta. 0 desativa",
        janela="Intervalo considerado. Ex.: 30s, 2m",
        acao="O que fazer ao detectar",
        duracao="Duração do bloqueio, se a ação for bloquear. Ex.: 15m",
    )
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="Somente alertar", value="alert"),
            app_commands.Choice(name="Bloquear servidor e alertar", value="lockdown"),
        ]
    )
    @is_staff()
    async def configurar(
        self,
        interaction: discord.Interaction,
        entradas: app_commands.Range[int, 0, 100],
        janela: str | None = None,
        acao: app_commands.Choice[str] | None = None,
        duracao: str | None = None,
    ) -> None:
        campos: dict[str, object] = {"raid_joins": entradas}

        if janela:
            segundos = int(parse_duration(janela).total_seconds())
            if not 5 <= segundos <= 3600:
                raise ZenibotError("A janela precisa ficar entre 5s e 1h.")
            campos["raid_window_s"] = segundos

        if acao:
            campos["raid_action"] = acao.value

        if duracao:
            minutos = int(parse_duration(duracao).total_seconds() // 60)
            if not 1 <= minutos <= 1440:
                raise ZenibotError("O bloqueio precisa durar entre 1 min e 24h.")
            campos["raid_lockdown_minutes"] = minutos

        await self.bot.db.set_config(interaction.guild_id, **campos)
        cfg = await self.bot.db.get_config(interaction.guild_id)

        if entradas == 0:
            await interaction.response.send_message(
                embed=embeds.ok("Anti-raid desativado."), ephemeral=True
            )
            return

        aviso = ""
        if (
            cfg.raid_action == "lockdown"
            and not interaction.guild.me.guild_permissions.manage_guild
        ):
            aviso = (
                "\n\n⚠️ Preciso da permissão **Gerenciar Servidor** para bloquear. "
                "Sem ela, só o alerta será enviado — reconvide-me com essa permissão."
            )

        if cfg.raid_action == "lockdown":
            descricao_acao = f"bloquear o servidor por {cfg.raid_lockdown_minutes} min"
        else:
            descricao_acao = "somente alertar"

        await interaction.response.send_message(
            embed=embeds.ok(
                f"Alerta ao detectar **{cfg.raid_joins} entradas em "
                f"{cfg.raid_window_s}s**.\n"
                f"Ação: {descricao_acao}.{aviso}"
            ),
            ephemeral=True,
        )

    @antiraid_group.command(name="liberar", description="Remove o bloqueio antes da hora")
    @is_staff()
    async def liberar(self, interaction: discord.Interaction) -> None:
        job = await self.bot.db.get_pending_job(interaction.guild_id, "raid_end")
        if job is None:
            raise ZenibotError("Não há bloqueio anti-raid ativo.")

        await interaction.response.defer(ephemeral=True)
        ok = await antiraid.lift_lockdown(interaction.guild, job.payload)
        await self.bot.db.mark_done(job.id)
        self.window.clear(interaction.guild_id)
        self._silence_until.pop(interaction.guild_id, None)

        restante = humanize(max(job.run_at - now(), timedelta(seconds=0)))
        await interaction.followup.send(
            embed=embeds.ok(f"Bloqueio liberado ({restante} antes do previsto).")
            if ok
            else embeds.error(
                "Não consegui restaurar as configurações — verifique se ainda "
                "tenho a permissão **Gerenciar Servidor**."
            ),
            ephemeral=True,
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(AntiRaid(bot))
