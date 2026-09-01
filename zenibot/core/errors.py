"""Resposta de erro única para slash commands e componentes.

Existe porque os dois caminhos falham de formas diferentes por padrão:

- Erro em slash command vai para `tree.on_error`, que nós tratamos.
- Erro em botão ou select vai para `View.on_error`, cuja implementação padrão
  **apenas registra no log** e nunca responde à interação. O usuário vê
  "o aplicativo não respondeu a tempo" e nada explica o porquê.

Centralizar aqui garante que os dois digam a mesma coisa.
"""

from __future__ import annotations

import logging
import traceback
import uuid

import discord
from discord import app_commands

from zenibot.core import embeds
from zenibot.core.checks import ZenibotError

log = logging.getLogger(__name__)


async def respond_error(
    interaction: discord.Interaction,
    error: BaseException,
    *,
    contexto: str,
) -> None:
    """Traduz o erro e responde ao usuário, sempre de forma efêmera.

    Erro esperado vira mensagem clara; inesperado vira um ID de correlação
    que aparece no log do servidor. O traceback nunca vai para o usuário: ele
    pode expor caminhos de arquivo e queries.
    """
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original

    if isinstance(error, ZenibotError):
        mensagem = str(error)
    elif isinstance(error, app_commands.CheckFailure):
        mensagem = "Você não tem permissão para usar isto."
    elif isinstance(error, app_commands.CommandOnCooldown):
        mensagem = f"Aguarde {error.retry_after:.0f}s antes de repetir."
    elif isinstance(error, discord.Forbidden):
        mensagem = (
            "Não tenho permissão para fazer isso. Verifique minhas permissões "
            "e a posição do meu cargo na hierarquia."
        )
    else:
        trace_id = uuid.uuid4().hex[:8]
        log.error(
            "[%s] Erro não tratado em %s (guild=%s user=%s)\n%s",
            trace_id,
            contexto,
            interaction.guild_id,
            interaction.user.id,
            "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        )
        mensagem = f"Erro interno. Referência para o suporte: `{trace_id}`"

    embed = embeds.error(mensagem)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        # Interação já expirada (3s) — nada a fazer além de registrar.
        log.warning("Não foi possível responder ao erro em %s", contexto)
