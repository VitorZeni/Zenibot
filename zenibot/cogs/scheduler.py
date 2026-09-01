"""Agendamentos persistentes.

O padrão central: **nada é agendado em memória**. Um `asyncio.sleep()` para um
lembrete de amanhã é um bug — o próximo deploy o apaga. Tudo vai para a tabela
`schedules` e um loop curto reivindica os jobs vencidos.
"""

from __future__ import annotations

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.db import Job, now
from zenibot.core.duration import humanize, parse_duration

log = logging.getLogger(__name__)

# Intervalo do poller. Curto o bastante para lembretes parecerem pontuais,
# longo o bastante para não pressionar o banco.
POLL_SECONDS = 30


class Scheduler(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot
        self.handlers = {
            "reminder": self.run_reminder,
            "unban": self.run_unban,
        }
        self.process_jobs.start()

    async def cog_unload(self) -> None:
        self.process_jobs.cancel()

    # ------------------------------------------------------------------
    # Poller
    # ------------------------------------------------------------------

    @tasks.loop(seconds=POLL_SECONDS)
    async def process_jobs(self) -> None:
        jobs = await self.bot.db.claim_due_jobs(limit=50)
        for job in jobs:
            handler = self.handlers.get(job.kind)
            if handler is None:
                log.error("Job %s tem kind desconhecido: %s", job.id, job.kind)
                await self.bot.db.mark_done(job.id)
                continue

            try:
                await handler(job)
                await self.bot.db.mark_done(job.id)
            except Exception as exc:  # noqa: BLE001 — um job ruim não derruba o loop
                log.exception("Falha no job %s (%s)", job.id, job.kind)
                await self.bot.db.reschedule_with_backoff(job.id, job.attempts, repr(exc))

    @process_jobs.before_loop
    async def before_process_jobs(self) -> None:
        """Obrigatório: sem isto o loop roda antes do cache de guilds existir
        e todo get_channel() devolve None."""
        await self.bot.wait_until_ready()

    @process_jobs.error
    async def on_loop_error(self, exc: BaseException) -> None:
        """Uma task de background que morre falha em silêncio por padrão.
        Logamos e reiniciamos."""
        log.exception("Loop do scheduler morreu; reiniciando", exc_info=exc)
        self.process_jobs.restart()

    # ------------------------------------------------------------------
    # Execução dos jobs
    # ------------------------------------------------------------------

    async def run_reminder(self, job: Job) -> None:
        texto = job.payload.get("text", "(sem texto)")
        criado_em = job.payload.get("created_at")

        embed = embeds.info(texto, title="Lembrete")
        if criado_em:
            embed.set_footer(text="Agendado em")
            embed.timestamp = datetime.fromisoformat(criado_em)

        channel = self.bot.get_channel(job.channel_id) if job.channel_id else None
        if channel is not None:
            try:
                await channel.send(
                    content=f"<@{job.user_id}>",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(id=job.user_id)]
                    ),
                )
                return
            except discord.HTTPException:
                log.warning("Canal %s indisponível; tentando DM", job.channel_id)

        # Fallback: DM. Pode falhar se o usuário tiver DMs fechadas.
        user = self.bot.get_user(job.user_id) or await self.bot.fetch_user(job.user_id)
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            log.info("Não foi possível entregar o lembrete %s ao usuário %s",
                     job.id, job.user_id)

    async def run_unban(self, job: Job) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            log.warning("Guild %s não está mais acessível; job %s descartado",
                        job.guild_id, job.id)
            return

        try:
            await guild.unban(
                discord.Object(id=job.user_id),
                reason="Fim do banimento temporário (Zenibot)",
            )
        except discord.NotFound:
            # Já foi desbanido manualmente: sucesso do ponto de vista do job.
            log.info("Usuário %s já não estava banido na guild %s", job.user_id, job.guild_id)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    @app_commands.command(name="lembrete", description="Agenda um lembrete para você")
    @app_commands.describe(quando="Ex.: 30m, 2h, 1h30m, 7d", texto="O que lembrar")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(3, 60.0)  # 3 por minuto, por usuário
    async def remind(
        self,
        interaction: discord.Interaction,
        quando: str,
        texto: app_commands.Range[str, 1, 500],
    ) -> None:
        delta = parse_duration(quando)
        run_at = now() + delta

        await self.bot.db.schedule(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
            kind="reminder",
            run_at=run_at,
            payload={"text": texto, "created_at": now().isoformat()},
        )

        await interaction.response.send_message(
            embed=embeds.ok(
                f"Lembrete agendado para {embeds.timestamp(run_at)} "
                f"(em {humanize(delta)}).\n\n> {texto}"
            ),
            ephemeral=True,
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Scheduler(bot))
