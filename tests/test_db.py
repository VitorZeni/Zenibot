"""Camada de persistência: configuração, casos, agendamentos, backup."""

from __future__ import annotations

from datetime import timedelta

import aiosqlite
import pytest

from zenibot.core.db import MIGRATIONS_DIR, Database, now, to_db

# ---------------------------------------------------------------------------
# Configuração por guild
# ---------------------------------------------------------------------------


async def test_config_padrao_e_criada_sob_demanda(db: Database) -> None:
    cfg = await db.get_config(42)
    assert cfg.guild_id == 42
    assert cfg.welcome_enabled is False
    assert cfg.staff_role_ids == []
    assert cfg.raid_joins == 0


async def test_config_persiste_alteracoes(db: Database) -> None:
    await db.set_config(42, log_channel_id=555, staff_role_ids=[7, 8], welcome_enabled=True)
    cfg = await db.get_config(42)
    assert cfg.log_channel_id == 555
    assert cfg.staff_role_ids == [7, 8]
    assert cfg.welcome_enabled is True


async def test_config_isola_guilds(db: Database) -> None:
    """Vazar configuração entre servidores é a pior falha de um bot multi-guild."""
    await db.set_config(42, log_channel_id=555)
    assert (await db.get_config(43)).log_channel_id is None


async def test_config_rejeita_coluna_fora_da_allowlist(db: Database) -> None:
    with pytest.raises(ValueError):
        await db.set_config(42, coluna_maliciosa="x")


async def test_config_antiraid(db: Database) -> None:
    await db.set_config(
        42, raid_joins=5, raid_window_s=30, raid_action="lockdown",
        raid_lockdown_minutes=20,
    )
    cfg = await db.get_config(42)
    assert (cfg.raid_joins, cfg.raid_window_s) == (5, 30)
    assert (cfg.raid_action, cfg.raid_lockdown_minutes) == ("lockdown", 20)


# ---------------------------------------------------------------------------
# Casos de moderação
# ---------------------------------------------------------------------------


async def caso(db: Database, guild_id: int = 42, user_id: int = 1, **kw) -> int:
    base = dict(moderator_id=2, action="warn", reason="spam")
    return await db.add_case(guild_id=guild_id, user_id=user_id, **{**base, **kw})


async def test_numeracao_sequencial_por_guild(db: Database) -> None:
    assert await caso(db) == 1
    assert await caso(db) == 2
    # Cada servidor tem sua própria sequência: o caso #1 de um não é o do outro.
    assert await caso(db, guild_id=43) == 1


async def test_historico_ordenado_do_mais_recente(db: Database) -> None:
    await caso(db)
    await caso(db, action="timeout", duration_s=600)
    numeros = [c.case_number for c in await db.get_user_cases(42, 1)]
    assert numeros == [2, 1]


async def test_busca_caso_por_numero(db: Database) -> None:
    await caso(db, reason="flood")
    encontrado = await db.get_case(42, 1)
    assert encontrado is not None and encontrado.reason == "flood"
    assert await db.get_case(42, 999) is None
    assert await db.get_case(43, 1) is None  # não vaza entre guilds


async def test_punicao_automatica_nao_conta_para_o_limiar(db: Database) -> None:
    """Contá-la faria a régua andar sozinha: a punição da 3ª infração viraria
    a 4ª, e o próximo aviso cairia numa regra mais dura que a merecida."""
    await caso(db)
    await caso(db)
    await caso(db, action="timeout", duration_s=3600, automatic=True)

    assert await db.count_active_cases(42, 1) == 2
    assert len(await db.get_user_cases(42, 1)) == 3  # mas segue auditável


async def test_contagem_respeita_a_janela(db: Database) -> None:
    await caso(db)
    assert await db.count_active_cases(42, 1, within_days=30) == 1
    assert await db.count_active_cases(42, 1, within_days=0) == 1


# ---------------------------------------------------------------------------
# Regras de escalonamento
# ---------------------------------------------------------------------------


async def test_regras_ordenadas_por_limiar(db: Database) -> None:
    await db.set_escalation_rule(42, 5, "ban", 604800)
    await db.set_escalation_rule(42, 3, "timeout", 3600)
    assert [r.threshold for r in await db.get_escalation_rules(42)] == [3, 5]


async def test_redefinir_regra_substitui_em_vez_de_duplicar(db: Database) -> None:
    await db.set_escalation_rule(42, 3, "timeout", 3600)
    await db.set_escalation_rule(42, 3, "kick", None)
    regras = await db.get_escalation_rules(42)
    assert len(regras) == 1
    assert regras[0].action == "kick"


async def test_remover_regra(db: Database) -> None:
    await db.set_escalation_rule(42, 3, "kick", None)
    assert await db.delete_escalation_rule(42, 3) is True
    assert await db.delete_escalation_rule(42, 3) is False


async def test_regras_isolam_guilds(db: Database) -> None:
    await db.set_escalation_rule(42, 3, "kick", None)
    assert await db.get_escalation_rules(43) == []


# ---------------------------------------------------------------------------
# Fila de agendamentos
# ---------------------------------------------------------------------------


async def test_reivindica_apenas_jobs_vencidos(db: Database) -> None:
    vencido = await db.schedule(
        guild_id=42, user_id=1, kind="reminder",
        run_at=now() - timedelta(minutes=1), payload={"text": "vencido"},
    )
    await db.schedule(
        guild_id=42, user_id=1, kind="reminder",
        run_at=now() + timedelta(days=1), payload={"text": "futuro"},
    )
    jobs = await db.claim_due_jobs()
    assert [j.id for j in jobs] == [vencido]
    assert jobs[0].payload["text"] == "vencido"


async def test_nao_entrega_o_mesmo_job_duas_vezes(db: Database) -> None:
    """A marcação claimed_at é o que impede execução dupla quando o loop
    demora mais que o intervalo."""
    await db.schedule(
        guild_id=42, user_id=1, kind="reminder", run_at=now() - timedelta(minutes=1)
    )
    assert len(await db.claim_due_jobs()) == 1
    assert await db.claim_due_jobs() == []


async def test_cancelar_jobs_pendentes(db: Database) -> None:
    await db.schedule(
        guild_id=42, user_id=1, kind="unban", run_at=now() + timedelta(days=1)
    )
    assert await db.cancel_jobs(guild_id=42, user_id=1, kind="unban") == 1
    assert await db.cancel_jobs(guild_id=42, user_id=1, kind="unban") == 0


async def test_backoff_desiste_apos_varias_tentativas(db: Database) -> None:
    job_id = await db.schedule(
        guild_id=42, user_id=1, kind="reminder", run_at=now() - timedelta(minutes=1)
    )
    await db.reschedule_with_backoff(job_id, attempts=4, error="boom")
    # 5ª tentativa: o job é encerrado em vez de reagendado para sempre.
    assert await db.claim_due_jobs() == []


async def test_job_pendente_guarda_o_payload(db: Database) -> None:
    """O estado a restaurar depois de um bloqueio anti-raid viaja aqui — é o
    que impede um restart de deixar o servidor trancado para sempre."""
    job_id = await db.schedule(
        guild_id=42, user_id=1, kind="raid_end",
        run_at=now() + timedelta(minutes=10),
        payload={"verification_level": 2, "invites_paused": False},
    )
    job = await db.get_pending_job(42, "raid_end")
    assert job is not None and job.id == job_id
    assert job.payload["verification_level"] == 2

    await db.mark_done(job_id)
    assert await db.get_pending_job(42, "raid_end") is None


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


async def test_backup_contem_os_dados(db: Database, tmp_path) -> None:
    """Uma cópia de arquivo sobre WAL passaria num teste de 'o arquivo existe'
    e falharia neste."""
    await caso(db)
    await caso(db)

    destino = tmp_path / "bkp" / "zenibot-20260101-000000.db"
    tamanho = await db.backup(destino)
    assert destino.exists() and tamanho > 0
    assert not destino.with_suffix(".partial").exists()

    async with aiosqlite.connect(destino) as copia:
        copia.row_factory = aiosqlite.Row
        cur = await copia.execute("SELECT COUNT(*) AS n FROM cases")
        assert (await cur.fetchone())["n"] == 2


# ---------------------------------------------------------------------------
# Migrações
# ---------------------------------------------------------------------------


async def test_migracao_sobre_banco_ja_povoado(tmp_path) -> None:
    """O caminho que quebra em produção: as migrações novas caem sobre um
    banco que já tem dados, não sobre um vazio."""
    legado = tmp_path / "legado.db"
    async with aiosqlite.connect(legado, isolation_level=None) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            (MIGRATIONS_DIR / "001_initial.sql").read_text(encoding="utf-8")
        )
        await conn.executescript(
            "CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);"
        )
        await conn.execute(
            "INSERT INTO _migrations VALUES ('001_initial.sql', ?)", (to_db(now()),)
        )
        await conn.execute(
            "INSERT INTO cases (guild_id, case_number, user_id, moderator_id,"
            " action, reason, created_at) VALUES (1, 1, 2, 3, 'warn', 'antigo', ?)",
            (to_db(now()),),
        )

    db = Database(legado)
    await db.connect()  # deve aplicar 002 e 003
    try:
        assert await db.count_active_cases(1, 2) == 1
        assert (await db.get_user_cases(1, 2))[0].reason == "antigo"
        assert await db.get_escalation_rules(1) == []
        assert (await db.get_config(1)).raid_joins == 0
        assert await db.list_templates(1) == []
    finally:
        await db.close()


async def test_migracoes_sao_idempotentes(settings) -> None:
    db = Database(settings.db_file)
    await db.connect()
    await db.migrate()  # segunda passada não deve reaplicar nada
    cur = await db.conn.execute("SELECT COUNT(*) AS n FROM _migrations")
    aplicadas = (await cur.fetchone())["n"]
    arquivos = len(list(MIGRATIONS_DIR.glob("*.sql")))
    await db.close()
    assert aplicadas == arquivos
