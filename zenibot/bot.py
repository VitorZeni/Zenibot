"""Classe principal do bot."""

from __future__ import annotations

import logging
import traceback
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.config import Settings
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError
from zenibot.core.db import Database

log = logging.getLogger(__name__)

INITIAL_COGS = (
    "zenibot.cogs.admin",
    "zenibot.cogs.moderation",
    "zenibot.cogs.welcome",
    "zenibot.cogs.logs",
    "zenibot.cogs.scheduler",
    "zenibot.cogs.selfroles",
    "zenibot.cogs.health",
)


def build_intents() -> discord.Intents:
    """Princípio do menor privilégio: habilite só o que é usado.

    MESSAGE_CONTENT (privilegiado) é deliberadamente omitido — todos os
    comandos são slash commands e a filtragem de conteúdo é delegada ao
    AutoMod nativo do Discord, que roda server-side.

    GUILD_MEMBERS é privilegiado e PRECISA ser habilitado no Developer Portal
    (aba Bot > Privileged Gateway Intents), senão on_member_join nunca dispara.
    """
    intents = discord.Intents.none()
    intents.guilds = True                     # cache de guilds/canais — essencial
    intents.members = True                    # PRIVILEGIADO: boas-vindas, autorole
    intents.moderation = True                 # bans + audit log entries
    intents.auto_moderation_execution = True  # reagir a acionamentos do AutoMod
    intents.guild_scheduled_events = True     # eventos nativos do servidor
    return intents


class Zenibot(commands.Bot):
    def __init__(self, settings: Settings, *, background_tasks: bool = True) -> None:
        """`background_tasks=False` para scripts utilitários.

        Sem isso, rodar sync_commands.py ou list_commands.py dispara o loop de
        backup — e, com a retenção ativa, backups espúrios de scripts acabam
        expulsando os legítimos da janela.
        """
        super().__init__(
            command_prefix=commands.when_mentioned,  # sem prefixo: tudo é slash
            intents=build_intents(),
            allowed_mentions=embeds.NO_MENTIONS,
            help_command=None,
            max_messages=None,  # sem cache de mensagens: não usamos o conteúdo
        )
        self.settings = settings
        self.background_tasks = background_tasks
        self.db = Database(settings.db_file)
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        """Roda uma vez, antes da conexão com o Gateway ser finalizada."""
        await self.db.connect()

        for cog in INITIAL_COGS:
            try:
                await self.load_extension(cog)
                log.info("Cog carregado: %s", cog)
            except Exception:
                log.exception("Falha ao carregar %s", cog)

        # Componentes persistentes: o cog de selfroles registra seu
        # DynamicItem no próprio setup(), então painéis antigos voltam a
        # responder sem que este módulo precise conhecê-los.
        # Views de layout fixo, se houver, iriam aqui com self.add_view().

        # NOTA: o registro de slash commands NÃO acontece aqui de propósito.
        # Sincronizar a cada boot desperdiça rate limit. Use scripts/sync.py
        # ou o comando /sync (restrito aos donos).

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info(
            "Conectado como %s (%s) em %d guild(s) · latência %.0fms",
            self.user,
            self.user.id,
            len(self.guilds),
            self.latency * 1000,
        )

    async def close(self) -> None:
        """Shutdown gracioso: fecha o WebSocket e só então o banco."""
        log.info("Encerrando...")
        await super().close()
        await self.db.close()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Erro esperado -> mensagem clara. Inesperado -> ID de correlação.

        Nunca devolvemos o traceback ao usuário: ele pode expor caminhos de
        arquivo, queries e, no pior caso, segredos.
        """
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original  # type: ignore[assignment]

        if isinstance(error, ZenibotError):
            message = str(error)
        elif isinstance(error, app_commands.CheckFailure):
            message = "Você não tem permissão para usar este comando."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Aguarde {error.retry_after:.0f}s antes de repetir este comando."
        elif isinstance(error, discord.Forbidden):
            message = (
                "Não tenho permissão para fazer isso. Verifique minhas permissões "
                "e a posição do meu cargo na hierarquia."
            )
        else:
            trace_id = uuid.uuid4().hex[:8]
            log.error(
                "[%s] Erro não tratado em /%s (guild=%s user=%s)\n%s",
                trace_id,
                interaction.command.qualified_name if interaction.command else "?",
                interaction.guild_id,
                interaction.user.id,
                "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            )
            message = f"Erro interno. Referência para o suporte: `{trace_id}`"

        embed = embeds.error(message)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            # Interação já expirada (3s) — nada a fazer além de registrar.
            log.warning("Não foi possível responder à interação com o erro.")
