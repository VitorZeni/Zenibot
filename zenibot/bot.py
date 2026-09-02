"""Classe principal do bot."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.config import Settings
from zenibot.core import embeds
from zenibot.core.db import Database
from zenibot.core.errors import respond_error

log = logging.getLogger(__name__)

INITIAL_COGS = (
    "zenibot.cogs.admin",
    "zenibot.cogs.moderation",
    "zenibot.cogs.welcome",
    "zenibot.cogs.logs",
    "zenibot.cogs.scheduler",
    "zenibot.cogs.selfroles",
    "zenibot.cogs.health",
    "zenibot.cogs.escalation",
    "zenibot.cogs.antiraid",
    "zenibot.cogs.builder",
    "zenibot.cogs.containers",
    "zenibot.cogs.templates",
    "zenibot.cogs.tempvoice",
    "zenibot.cogs.tickets",
    "zenibot.cogs.channels",
    "zenibot.cogs.party",
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
    # Não é privilegiado, mas sem ele on_voice_state_update nunca dispara e a
    # voz temporária fica muda — sem erro nenhum.
    intents.voice_states = True
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
        nome = interaction.command.qualified_name if interaction.command else "?"
        await respond_error(interaction, error, contexto=f"/{nome}")
