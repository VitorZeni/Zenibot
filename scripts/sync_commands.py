"""Registra os slash commands no Discord.

Deliberadamente separado do boot do bot: sincronizar a cada inicialização
desperdiça rate limit sem necessidade, já que os comandos raramente mudam.

    python scripts/sync_commands.py           # guild de dev (instantâneo)
    python scripts/sync_commands.py --global  # produção (propagação lenta)
    python scripts/sync_commands.py --clear   # remove os comandos da guild de dev

Registro global pode levar até uma hora para propagar; registro por guild é
imediato — por isso ZENIBOT_DEV_GUILD_ID existe.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord  # noqa: E402

from zenibot.bot import Zenibot  # noqa: E402
from zenibot.config import load_settings  # noqa: E402
from zenibot.core.logging_setup import setup_logging  # noqa: E402


async def main(escopo_global: bool, limpar: bool) -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not escopo_global and settings.dev_guild_id is None:
        print(
            "ZENIBOT_DEV_GUILD_ID não está definido no .env.\n"
            "Defina-o para sincronizar numa guild, ou use --global.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    bot = Zenibot(settings, background_tasks=False)

    async with bot:
        # login() já dispara o setup_hook(), que conecta o banco e carrega os
        # cogs. Fazer isso manualmente aqui antes causaria
        # ExtensionAlreadyLoaded nos cinco cogs. Não abrimos o Gateway: só
        # precisamos da árvore de comandos montada.
        await bot.login(settings.token)

        if escopo_global:
            if limpar:
                bot.tree.clear_commands(guild=None)
            comandos = await bot.tree.sync()
            print(f"{len(comandos)} comando(s) sincronizado(s) globalmente.")
            print("A propagação global pode levar até 1 hora.")
        else:
            guild = discord.Object(id=settings.dev_guild_id)
            if limpar:
                bot.tree.clear_commands(guild=guild)
            else:
                bot.tree.copy_global_to(guild=guild)
            try:
                comandos = await bot.tree.sync(guild=guild)
            except discord.Forbidden as exc:
                # 50001 aqui quase sempre significa convite sem o escopo
                # applications.commands — o bot está no servidor, mas não tem
                # direito de registrar slash commands nele.
                print(
                    f"\n403 Missing Access ao registrar na guild "
                    f"{settings.dev_guild_id}.\n\n"
                    "Causa provável: o bot foi convidado sem o escopo "
                    "'applications.commands'.\n"
                    "Gere um novo convite no Developer Portal (OAuth2 > URL "
                    "Generator) marcando\n"
                    "os escopos 'bot' E 'applications.commands', e abra a URL "
                    "para o mesmo servidor.\n"
                    "Reconvidar não remove o bot nem apaga nada — apenas "
                    "concede o escopo faltante.\n",
                    file=sys.stderr,
                )
                raise SystemExit(1) from exc
            print(f"{len(comandos)} comando(s) sincronizado(s) na guild {settings.dev_guild_id}.")

        for cmd in sorted(c.name for c in comandos):
            print(f"  /{cmd}")

        await avisar_duplicidade(bot, settings, escopo_global)


async def avisar_duplicidade(bot: Zenibot, settings, escopo_global: bool) -> None:
    """Alerta se os comandos também existem no outro escopo.

    Um comando registrado global E por guild aparece DUAS VEZES na lista do
    cliente do Discord. É silencioso — nada falha, só fica duplicado.
    """
    if escopo_global:
        if settings.dev_guild_id is None:
            return
        alvo = discord.Object(id=settings.dev_guild_id)
        outros = {c.name for c in await bot.tree.fetch_commands(guild=alvo)}
        onde = f"na guild {settings.dev_guild_id}"
        remedio = "python scripts/sync_commands.py --clear"
    else:
        outros = {c.name for c in await bot.tree.fetch_commands()}
        onde = "globalmente"
        remedio = "python scripts/sync_commands.py --global --clear"

    if outros:
        print(
            f"\nAVISO: {len(outros)} comando(s) também registrado(s) {onde}.\n"
            "Comandos presentes nos dois escopos aparecem DUPLICADOS no Discord.\n"
            f"Para remover os do outro escopo:\n\n    {remedio}\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincroniza os slash commands do Zenibot")
    parser.add_argument("--global", dest="escopo_global", action="store_true",
                        help="registrar globalmente em vez de na guild de dev")
    parser.add_argument("--clear", action="store_true",
                        help="remover os comandos em vez de registrá-los")
    args = parser.parse_args()
    asyncio.run(main(args.escopo_global, args.clear))
