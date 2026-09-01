"""Operação: backup automático do banco e aviso de inicialização.

As duas coisas existem pelo mesmo motivo — num bot pessoal ninguém além de
você percebe quando algo para. Um backup que ninguém agendou e uma queda que
ninguém notou são o mesmo tipo de falha.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zenibot import __version__
from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.checks import is_owner
from zenibot.core.db import now

log = logging.getLogger(__name__)

BACKUP_GLOB = "zenibot-*.db"
BACKUP_STAMP = "%Y%m%d-%H%M%S"


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


class Health(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot
        # on_ready dispara de novo a cada reconexão do Gateway; sem esta trava
        # o canal receberia um "estou no ar" a cada oscilação de rede.
        self._announced = False

        horas = bot.settings.backup_interval_hours
        if horas > 0:
            self.backup_loop.change_interval(hours=horas)
            self.backup_loop.start()
        else:
            log.info("Backup automático desativado (ZENIBOT_BACKUP_INTERVAL_HOURS=0)")

    async def cog_unload(self) -> None:
        self.backup_loop.cancel()

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def run_backup(self) -> tuple[str, int, int]:
        """Executa um backup e poda os antigos. Devolve (nome, bytes, podados)."""
        destino = (
            self.bot.settings.backup_dir / f"zenibot-{now():{BACKUP_STAMP}}.db"
        )
        tamanho = await self.bot.db.backup(destino)
        podados = self.prune()
        return destino.name, tamanho, podados

    def prune(self) -> int:
        """Mantém apenas os N backups mais recentes."""
        manter = self.bot.settings.backup_keep
        if manter <= 0:
            return 0
        # O carimbo YYYYMMDD-HHMMSS torna a ordem alfabética idêntica à
        # cronológica, então sorted() basta — sem depender de mtime.
        arquivos = sorted(self.bot.settings.backup_dir.glob(BACKUP_GLOB))
        excedentes = arquivos[:-manter]
        for arquivo in excedentes:
            arquivo.unlink(missing_ok=True)
        return len(excedentes)

    @tasks.loop(hours=24)  # intervalo real vem de settings, via change_interval
    async def backup_loop(self) -> None:
        try:
            nome, tamanho, podados = await self.run_backup()
            log.info(
                "Backup criado: %s (%s); %d antigo(s) removido(s)",
                nome,
                human_size(tamanho),
                podados,
            )
        except Exception:
            # Disco cheio ou permissão negada não podem matar o loop: o
            # próximo ciclo tenta de novo.
            log.exception("Falha ao gerar backup")

    @backup_loop.before_loop
    async def before_backup(self) -> None:
        """Deliberadamente NÃO espera o Gateway.

        Backup não fala com o Discord. Se a API estiver fora do ar, é
        justamente quando não se quer perder o backup do dia.
        """

    @backup_loop.error
    async def on_backup_error(self, exc: BaseException) -> None:
        log.exception("Loop de backup morreu; reiniciando", exc_info=exc)
        self.backup_loop.restart()

    @app_commands.command(name="backup", description="[dono] Gera um backup do banco agora")
    @is_owner()
    async def backup_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        nome, tamanho, podados = await self.run_backup()
        restantes = len(sorted(self.bot.settings.backup_dir.glob(BACKUP_GLOB)))
        await interaction.followup.send(
            embed=embeds.ok(
                f"Backup criado: `{nome}` ({human_size(tamanho)}).\n"
                f"{restantes} backup(s) guardado(s), {podados} removido(s)."
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Aviso de inicialização
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._announced or not self.bot.settings.startup_notice:
            return
        self._announced = True

        settings = self.bot.settings
        try:
            tamanho_db = human_size(settings.db_file.stat().st_size)
        except OSError:
            tamanho_db = "desconhecido"

        if settings.backup_interval_hours > 0:
            backup_txt = (
                f"a cada {settings.backup_interval_hours}h, "
                f"mantendo {settings.backup_keep}"
            )
        else:
            backup_txt = "desativado"

        embed = embeds.ok(
            f"Zenibot **v{__version__}** está no ar.",
            title="Bot iniciado",
        )
        embed.add_field(name="Latência", value=f"{self.bot.latency * 1000:.0f}ms", inline=True)
        embed.add_field(name="Banco", value=tamanho_db, inline=True)
        embed.add_field(name="Backup", value=backup_txt, inline=True)
        embed.timestamp = now()

        # Só avisa onde há canal configurado — o aviso é opt-in por natureza.
        for guild in self.bot.guilds:
            cfg = await self.bot.db.get_config(guild.id)
            canal_id = cfg.alert_channel_id or cfg.log_channel_id
            if not canal_id:
                continue
            canal = guild.get_channel(canal_id)
            if canal is None:
                continue
            try:
                await canal.send(embed=embed, allowed_mentions=embeds.NO_MENTIONS)
            except discord.HTTPException:
                log.warning("Não consegui avisar a inicialização na guild %s", guild.id)


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Health(bot))
