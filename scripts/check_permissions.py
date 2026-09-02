"""Confere as permissões reais do bot no servidor, por funcionalidade.

Somente leitura. Útil depois de reconvidar o bot, para confirmar que a
permissão nova chegou — e para descobrir o que falta antes de implementar
algo novo.

    python scripts/check_permissions.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zenibot.bot import Zenibot  # noqa: E402
from zenibot.config import load_settings  # noqa: E402
from zenibot.core.logging_setup import setup_logging  # noqa: E402

# O que cada funcionalidade exige, em permissões de servidor.
RECURSOS: dict[str, list[str]] = {
    "Moderação (/aviso, /silenciar, /banir)": [
        "moderate_members",
        "kick_members",
        "ban_members",
    ],
    "Logs de auditoria": ["view_audit_log"],
    "Boas-vindas e autorole": ["manage_roles"],
    "Painéis de self-role": ["manage_roles"],
    "Construtores de mensagem": ["send_messages", "embed_links"],
    "Canais por cargo e tickets": ["manage_channels"],
    "Voz temporária": ["manage_channels", "move_members", "connect"],
    "Bloqueio anti-raid": ["manage_guild"],
}


async def main() -> int:
    settings = load_settings()
    setup_logging("ERROR")

    if settings.dev_guild_id is None:
        print("Defina ZENIBOT_DEV_GUILD_ID no .env.", file=sys.stderr)
        return 1

    bot = Zenibot(settings, background_tasks=False)
    async with bot:
        await bot.login(settings.token)
        guild = await bot.fetch_guild(settings.dev_guild_id)
        eu = await guild.fetch_member(bot.user.id)
        perms = eu.guild_permissions

        print(f"{guild.name} · cargo mais alto: {eu.top_role.name}\n")

        faltando_algo = False
        for recurso, exigidas in RECURSOS.items():
            faltam = [p for p in exigidas if not getattr(perms, p)]
            if faltam:
                faltando_algo = True
                print(f"  FALTA  {recurso}")
                print(f"         sem: {', '.join(faltam)}")
            else:
                print(f"  OK     {recurso}")

        print()
        if perms.administrator:
            print(
                "AVISO: o bot está com Administrador. As checagens de hierarquia\n"
                "       continuam valendo, mas a contenção de permissões some."
            )
        print(f"valor atual das permissões: {perms.value}")
        return 1 if faltando_algo else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
