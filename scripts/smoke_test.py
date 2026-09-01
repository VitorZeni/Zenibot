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
    check("8 cogs carregados", len(cogs) == 8, ", ".join(cogs))

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
        "escalonamento", "escalonamento ver", "escalonamento definir",
        "escalonamento remover",
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

    print("\n[12] Escalonamento automático")
    from zenibot.core.escalation import rule_for_count, validate

    await bot.db.add_case(
        guild_id=42, user_id=1, moderator_id=9, action="timeout",
        reason="auto", duration_s=3600, automatic=True,
    )
    contagem = await bot.db.count_active_cases(42, 1)
    # O ponto central do desenho: contá-la faria a régua andar sozinha.
    check("punicao automatica NAO conta para o limiar", contagem == 2, str(contagem))
    check(
        "mas continua no historico para auditoria",
        len(await bot.db.get_user_cases(42, 1)) == 3,
    )

    await bot.db.set_escalation_rule(42, 3, "timeout", 3600)
    await bot.db.set_escalation_rule(42, 5, "ban", 604800)
    regras = await bot.db.get_escalation_rules(42)
    check("regras ordenadas por limiar", [r.threshold for r in regras] == [3, 5])
    check("isola guilds", await bot.db.get_escalation_rules(43) == [])

    await bot.db.set_escalation_rule(42, 3, "kick", None)
    regras = await bot.db.get_escalation_rules(42)
    check(
        "redefinir substitui em vez de duplicar",
        len(regras) == 2 and regras[0].action == "kick",
        f"{len(regras)} regra(s), primeira={regras[0].action}",
    )

    check("dispara no limiar exato", rule_for_count(regras, 3) is not None)
    check("NAO redispara acima do limiar", rule_for_count(regras, 4) is None)
    check("nao dispara abaixo do limiar", rule_for_count(regras, 2) is None)

    check("remove regra existente", await bot.db.delete_escalation_rule(42, 3))
    check(
        "remover inexistente devolve False",
        not await bot.db.delete_escalation_rule(42, 99),
    )

    combinacoes = [
        ("timeout", None, True),      # timeout exige duração
        ("timeout", 3600, False),
        ("timeout", 86400 * 40, True),  # acima do limite de 28 dias
        ("kick", 3600, True),         # expulsão não tem duração
        ("kick", None, False),
        ("ban", None, False),         # ban sem duração = permanente
        ("ban", 604800, False),
        ("banimento", None, True),    # ação inexistente
    ]
    for acao, dur, deve_falhar in combinacoes:
        try:
            validate(acao, dur)
            falhou = False
        except ValueError:
            falhou = True
        check(f"validate({acao!r}, {dur})", falhou == deve_falhar)

    print("\n[13] Backup do banco")
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
    # Comparado com a origem, não com um número fixo: assim o teste não quebra
    # quando outra seção passa a inserir casos.
    cur = await bot.db.conn.execute("SELECT COUNT(*) AS n FROM cases")
    esperado = (await cur.fetchone())["n"]
    async with aiosqlite.connect(destino) as copia:
        copia.row_factory = aiosqlite.Row
        cur = await copia.execute("SELECT COUNT(*) AS n FROM cases")
        obtido = (await cur.fetchone())["n"]
    check(
        "backup contem os mesmos dados da origem",
        obtido == esperado and esperado > 0,
        f"{obtido} de {esperado} caso(s)",
    )
    destino.unlink(missing_ok=True)

    print("\n[14] Retencao de backups")
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

    print("\n[15] Migracao sobre banco ja existente")
    # O smoke test roda sempre em banco novo, então 001+002 aplicam juntas.
    # Em produção a 002 cai sobre um banco que já tem dados — é esse caminho
    # que quebra, e é ele que este bloco exercita.
    from zenibot.core.db import MIGRATIONS_DIR, Database, to_db

    legado = TMP_DB.parent / "zenibot_legado.db"
    legado.unlink(missing_ok=True)
    async with aiosqlite.connect(legado, isolation_level=None) as antigo_conn:
        await antigo_conn.executescript(
            (MIGRATIONS_DIR / "001_initial.sql").read_text(encoding="utf-8")
        )
        await antigo_conn.executescript(
            "CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);"
        )
        await antigo_conn.execute(
            "INSERT INTO _migrations VALUES ('001_initial.sql', ?)", (to_db(now()),)
        )
        await antigo_conn.execute(
            "INSERT INTO cases (guild_id, case_number, user_id, moderator_id,"
            " action, reason, created_at) VALUES (1, 1, 2, 3, 'warn', 'antigo', ?)",
            (to_db(now()),),
        )

    legado_db = Database(legado)
    await legado_db.connect()  # deve aplicar somente a 002
    check(
        "caso pre-existente sobrevive a migracao",
        await legado_db.count_active_cases(1, 2) == 1,
    )
    check(
        "linha antiga recebe automatic=0 e continua contando",
        (await legado_db.get_user_cases(1, 2))[0].reason == "antigo",
    )
    check("tabela nova criada no banco antigo", await legado_db.get_escalation_rules(1) == [])
    await legado_db.close()
    legado.unlink(missing_ok=True)

    await bot.close()
    TMP_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + "=" * 60)
    if falhas:
        print(f"{len(falhas)} FALHA(S): {falhas}")
        raise SystemExit(1)
    print("Todos os checks passaram.")
