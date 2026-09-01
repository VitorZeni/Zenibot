"""Smoke test: exercita tudo que acontece antes de conectar ao Gateway.

Não precisa de token nem de rede — usa um banco temporário e credenciais
falsas. Rode depois de mexer em cogs, migrações ou na camada de banco:

    python scripts/smoke_test.py

O que ele NÃO cobre: a conexão com o Gateway em si e o on_ready. Para isso é
preciso um token válido e `python -m zenibot`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_DB = Path(tempfile.gettempdir()) / "zenibot_smoke.db"
TMP_DB.unlink(missing_ok=True)

os.environ["ZENIBOT_TOKEN"] = "x" * 60          # formato válido, não autentica
os.environ["ZENIBOT_OWNER_IDS"] = "111,222"
os.environ["ZENIBOT_DEV_GUILD_ID"] = "999"
os.environ["ZENIBOT_DB_PATH"] = str(TMP_DB)
os.environ["ZENIBOT_LOG_LEVEL"] = "WARNING"

falhas: list[str] = []


def check(nome: str, condicao: bool, detalhe: str = "") -> None:
    print(f"  {'OK  ' if condicao else 'FALHA'} {nome}{(' — ' + detalhe) if detalhe else ''}")
    if not condicao:
        falhas.append(nome)


async def main() -> None:
    from datetime import timedelta

    from zenibot.bot import Zenibot, build_intents
    from zenibot.config import load_settings
    from zenibot.core.db import now
    from zenibot.core.duration import humanize, parse_duration
    from zenibot.core.logging_setup import RedactSecrets, setup_logging

    print("\n[1] Configuração")
    settings = load_settings()
    setup_logging(settings.log_level)
    check("carrega .env/ambiente", settings.dev_guild_id == 999)
    check("parse de owner_ids", settings.owner_ids == {111, 222}, str(settings.owner_ids))

    print("\n[2] Intents")
    intents = build_intents()
    check("members habilitado (privilegiado)", intents.members)
    check("moderation habilitado", intents.moderation)
    check("message_content DESABILITADO", not intents.message_content)

    print("\n[3] Boot: migrações + carga de cogs")
    # background_tasks=False: um script não deve disparar backup nem o poller.
    bot = Zenibot(settings, background_tasks=False)
    # "async with" inicializa o cliente do mesmo jeito que o entrypoint real.
    await bot.__aenter__()
    await bot.setup_hook()
    cogs = sorted(bot.cogs)
    check("7 cogs carregados", len(cogs) == 7, ", ".join(cogs))

    print("\n[4] Árvore de comandos")
    comandos = sorted(c.qualified_name for c in bot.tree.walk_commands())
    for nome in comandos:
        print(f"       /{nome}")
    esperados = {
        "aviso", "silenciar", "expulsar", "banir", "desbanir", "caso", "historico",
        "ping", "sync", "reload", "lembrete",
        "config", "config ver", "config canal", "config boas-vindas",
        "config autorole", "config idade-minima", "config staff",
        "painel", "painel criar", "painel adicionar", "painel remover",
        "backup",
    }
    faltando = esperados - set(comandos)
    check(f"{len(comandos)} comandos registrados", not faltando, f"faltando: {faltando}")

    print("\n[5] Parsing de duração")
    casos = [("30m", 1800), ("2h", 7200), ("1h30m", 5400), ("7d", 604800), ("1sem", 604800)]
    for texto, segundos in casos:
        obtido = int(parse_duration(texto).total_seconds())
        check(f"{texto!r} -> {segundos}s", obtido == segundos, f"obtido {obtido}s")
    check("humanize(90min)", humanize(timedelta(minutes=90)) == "1 hora, 30 minutos",
          humanize(timedelta(minutes=90)))
    try:
        parse_duration("banana")
        check("rejeita entrada inválida", False)
    except Exception as exc:
        check("rejeita entrada inválida", "inválida" in str(exc))

    print("\n[6] Banco: config por guild")
    cfg = await bot.db.get_config(42)
    check("cria config padrão", cfg.guild_id == 42 and not cfg.welcome_enabled)
    await bot.db.set_config(42, log_channel_id=555, staff_role_ids=[7, 8], welcome_enabled=True)
    cfg = await bot.db.get_config(42)
    check("persiste alterações", cfg.log_channel_id == 555 and cfg.staff_role_ids == [7, 8])
    check("isola guilds", (await bot.db.get_config(43)).log_channel_id is None)
    try:
        await bot.db.set_config(42, coluna_maliciosa="x")
        check("bloqueia coluna fora da allow-list", False)
    except ValueError:
        check("bloqueia coluna fora da allow-list", True)

    print("\n[7] Banco: casos de moderação")
    n1 = await bot.db.add_case(guild_id=42, user_id=1, moderator_id=2,
                               action="warn", reason="spam")
    n2 = await bot.db.add_case(guild_id=42, user_id=1, moderator_id=2,
                               action="timeout", reason="flood", duration_s=600)
    n3 = await bot.db.add_case(guild_id=43, user_id=1, moderator_id=2,
                               action="warn", reason="outro servidor")
    check("numeração sequencial por guild", (n1, n2) == (1, 2), f"{n1}, {n2}")
    check("guild 43 recomeça em 1", n3 == 1, str(n3))
    check("contagem de infrações ativas", await bot.db.count_active_cases(42, 1) == 2)
    check("histórico ordenado desc",
          [c.case_number for c in await bot.db.get_user_cases(42, 1)] == [2, 1])

    print("\n[8] Banco: fila de agendamentos")
    passado = await bot.db.schedule(guild_id=42, user_id=1, kind="reminder",
                                    run_at=now() - timedelta(minutes=1),
                                    payload={"text": "vencido"})
    await bot.db.schedule(guild_id=42, user_id=1, kind="reminder",
                          run_at=now() + timedelta(days=1), payload={"text": "futuro"})
    jobs = await bot.db.claim_due_jobs()
    check("reivindica só os vencidos", len(jobs) == 1 and jobs[0].id == passado,
          f"{len(jobs)} job(s)")
    check("payload JSON preservado", jobs and jobs[0].payload["text"] == "vencido")
    check("não reivindica duas vezes", await bot.db.claim_due_jobs() == [])
    await bot.db.mark_done(passado)
    cancelados = await bot.db.cancel_jobs(guild_id=42, user_id=1, kind="reminder")
    check("cancela jobs pendentes", cancelados == 1, f"{cancelados} cancelado(s)")

    print("\n[9] Redação de segredos no log")
    import logging

    # O token falso é montado em tempo de execução de propósito: escrito
    # inteiro no código, ele tem formato válido o bastante para o secret
    # scanning do GitHub bloquear o push (GH013).
    token_falso = ".".join(
        ["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GaBcDe", "abcdefghijklmnopqrstuvwxyz123"]
    )
    rec = logging.LogRecord("t", logging.INFO, "", 0, f"token={token_falso}", None, None)
    RedactSecrets().filter(rec)
    check("token filtrado do log", "REDACTED" in rec.msg, rec.msg)

    print("\n[10] Self-roles: bloqueio de cargos perigosos")
    from types import SimpleNamespace

    import discord

    from zenibot.cogs.selfroles import RoleToggleButton, dangerous_permissions

    def cargo_falso(**perms):
        return SimpleNamespace(permissions=discord.Permissions(**perms))

    check(
        "detecta administrator",
        dangerous_permissions(cargo_falso(administrator=True)) == ["administrator"],
    )
    check(
        "detecta varias permissoes de risco",
        set(dangerous_permissions(cargo_falso(ban_members=True, manage_roles=True)))
        == {"manage_roles", "ban_members"},
    )
    check(
        "cargo cosmetico e liberado",
        dangerous_permissions(cargo_falso(send_messages=True, view_channel=True)) == [],
    )

    print("\n[11] Self-roles: persistencia via custom_id")
    template = RoleToggleButton.__discord_ui_compiled_template__
    casou = template.fullmatch("srole:123456789012345678")
    check(
        "template casa um custom_id de painel",
        casou is not None and casou["role_id"] == "123456789012345678",
    )
    check("template ignora outros componentes", template.fullmatch("outro:123") is None)
    botao = RoleToggleButton(999, label="Notificações")
    check(
        "botao gera o custom_id esperado",
        botao.item.custom_id == "srole:999",
        botao.item.custom_id or "",
    )
    check("view persistente nao expira", discord.ui.View(timeout=None).timeout is None)

    print("\n[12] Backup do banco")
    import aiosqlite

    health = bot.get_cog("Health")
    # Regressão: um script utilitário disparava backup só por instanciar o bot,
    # e com a retenção ativa isso expulsava backups legítimos da janela.
    check("loop de backup parado em script", not health.backup_loop.is_running())
    check(
        "poller do scheduler parado em script",
        not bot.get_cog("Scheduler").process_jobs.is_running(),
    )

    destino = TMP_DB.parent / "zenibot_bkp" / "zenibot-20260101-000000.db"
    tamanho = await bot.db.backup(destino)
    check("backup criado", destino.exists() and tamanho > 0, f"{tamanho} bytes")
    check(
        "nao deixa arquivo .partial para tras",
        not destino.with_suffix(".partial").exists(),
    )

    # O que importa não é o arquivo existir, e sim conter os dados: um backup
    # feito com cópia de arquivo sobre WAL passaria no teste acima e falharia
    # neste.
    async with aiosqlite.connect(destino) as copia:
        copia.row_factory = aiosqlite.Row
        cur = await copia.execute("SELECT COUNT(*) AS n FROM cases WHERE guild_id = 42")
        linha = await cur.fetchone()
    check("backup contem os dados", linha["n"] == 2, f"{linha['n']} caso(s)")
    destino.unlink(missing_ok=True)

    print("\n[13] Retencao de backups")
    bdir = settings.backup_dir
    bdir.mkdir(parents=True, exist_ok=True)
    for antigo in bdir.glob("zenibot-*.db"):
        antigo.unlink()
    for i in range(10):
        (bdir / f"zenibot-20260101-0000{i:02d}.db").write_bytes(b"x")

    podados = health.prune()
    restantes = sorted(p.name for p in bdir.glob("zenibot-*.db"))
    check(
        f"mantem apenas os {settings.backup_keep} mais novos",
        len(restantes) == settings.backup_keep and podados == 3,
        f"{len(restantes)} restantes, {podados} podados",
    )
    check(
        "descarta os mais antigos, nao os recentes",
        restantes[0] == "zenibot-20260101-000003.db",
        restantes[0],
    )
    for f in bdir.glob("zenibot-*.db"):
        f.unlink()

    await bot.close()
    TMP_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + "=" * 60)
    if falhas:
        print(f"{len(falhas)} FALHA(S): {falhas}")
        raise SystemExit(1)
    print("Todos os checks passaram.")
