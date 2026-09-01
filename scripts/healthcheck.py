"""Verificação de deploy: o bot consegue subir neste ambiente?

Exercita config, migrações, carga dos cogs e árvore de comandos. Não precisa
de token válido nem de rede — serve para validar uma imagem Docker recém-
construída, onde a suíte pytest (dependência de desenvolvimento) não existe.

    python scripts/healthcheck.py

Para testar o comportamento do código, use a suíte: `pytest`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ZENIBOT_TOKEN", "x" * 60)
os.environ.setdefault("ZENIBOT_OWNER_IDS", "1")
os.environ["ZENIBOT_DB_PATH"] = str(Path(tempfile.gettempdir()) / "zenibot_health.db")
os.environ["ZENIBOT_LOG_LEVEL"] = "WARNING"


async def main() -> int:
    from zenibot.bot import INITIAL_COGS, Zenibot, build_intents
    from zenibot.config import load_settings

    settings = load_settings()
    banco = settings.db_file
    banco.unlink(missing_ok=True)

    bot = Zenibot(settings, background_tasks=False)
    await bot.__aenter__()
    await bot.setup_hook()

    cogs = sorted(bot.cogs)
    comandos = sorted(c.qualified_name for c in bot.tree.walk_commands())
    intents = build_intents()

    await bot.close()
    banco.unlink(missing_ok=True)

    print(f"cogs      : {len(cogs)}/{len(INITIAL_COGS)} — {', '.join(cogs)}")
    print(f"comandos  : {len(comandos)}")
    print(f"intents   : members={intents.members} message_content={intents.message_content}")

    if len(cogs) != len(INITIAL_COGS):
        print("FALHA: nem todos os cogs carregaram", file=sys.stderr)
        return 1
    if not comandos:
        print("FALHA: árvore de comandos vazia", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
