"""Lista os slash commands registrados no Discord, por escopo.

Somente leitura: não registra nem remove nada.

    python scripts/list_commands.py

Comando que aparece nos DOIS escopos é exibido duas vezes no cliente do
Discord. É a causa mais comum de comandos duplicados.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord  # noqa: E402

from zenibot.bot import Zenibot  # noqa: E402
from zenibot.config import load_settings  # noqa: E402
from zenibot.core.logging_setup import setup_logging  # noqa: E402


async def main() -> None:
    settings = load_settings()
    setup_logging("WARNING")

    bot = Zenibot(settings, background_tasks=False)
    async with bot:
        await bot.login(settings.token)

        globais = {c.name for c in await bot.tree.fetch_commands()}
        print(f"GLOBAL ({len(globais)}):")
        for nome in sorted(globais) or ["  (nenhum)"]:
            print(f"  /{nome}")

        por_guild: dict[int, set[str]] = {}
        if settings.dev_guild_id:
            alvo = discord.Object(id=settings.dev_guild_id)
            nomes = {c.name for c in await bot.tree.fetch_commands(guild=alvo)}
            por_guild[settings.dev_guild_id] = nomes
            print(f"\nGUILD {settings.dev_guild_id} ({len(nomes)}):")
            for nome in sorted(nomes) or ["  (nenhum)"]:
                print(f"  /{nome}")

        print()
        for guild_id, nomes in por_guild.items():
            duplicados = globais & nomes
            if duplicados:
                print(
                    f"DUPLICADOS na guild {guild_id} "
                    f"({len(duplicados)}): {', '.join(sorted(duplicados))}"
                )
                print(
                    "\nEstes comandos estão registrados nos dois escopos e por "
                    "isso aparecem em dobro.\nPara um bot de poucos servidores, "
                    "o escopo de guild é melhor (propaga na hora).\nRemova os "
                    "globais com:\n\n"
                    "    python scripts/sync_commands.py --global --clear\n"
                )
            else:
                print(f"Sem sobreposição entre global e guild {guild_id}.")


if __name__ == "__main__":
    asyncio.run(main())
