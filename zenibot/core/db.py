"""Camada de persistência.

Todo o SQL do projeto vive aqui. Isso é deliberado: quando o bot crescer o
suficiente para exigir PostgreSQL (sharding, múltiplos processos), este é o
único módulo que precisa mudar. Ver README, seção "Migrando para PostgreSQL".

Regra de ouro respeitada em todas as queries: **filtrar sempre por guild_id**.
Vazar configuração ou histórico de moderação entre servidores é a falha mais
grave que um bot multi-guild pode ter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

# Um job reivindicado mas não concluído é liberado depois disto (o processo
# provavelmente morreu no meio da execução).
CLAIM_TTL = timedelta(minutes=5)


def now() -> datetime:
    return datetime.now(UTC)


def to_db(dt: datetime) -> str:
    """datetime -> string ISO-8601 UTC de largura fixa."""
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_db(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    log_channel_id: int | None = None
    alert_channel_id: int | None = None
    welcome_channel_id: int | None = None
    welcome_enabled: bool = False
    autorole_id: int | None = None
    min_account_age_days: int = 0
    staff_role_ids: list[int] = field(default_factory=list)
    raid_joins: int = 0
    raid_window_s: int = 60
    raid_action: str = "alert"
    raid_lockdown_minutes: int = 15

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> GuildConfig:
        return cls(
            guild_id=row["guild_id"],
            log_channel_id=row["log_channel_id"],
            alert_channel_id=row["alert_channel_id"],
            welcome_channel_id=row["welcome_channel_id"],
            welcome_enabled=bool(row["welcome_enabled"]),
            autorole_id=row["autorole_id"],
            min_account_age_days=row["min_account_age_days"],
            staff_role_ids=json.loads(row["staff_role_ids"]),
            raid_joins=row["raid_joins"],
            raid_window_s=row["raid_window_s"],
            raid_action=row["raid_action"],
            raid_lockdown_minutes=row["raid_lockdown_minutes"],
        )


@dataclass(slots=True)
class Case:
    case_number: int
    guild_id: int
    user_id: int
    moderator_id: int
    action: str
    reason: str
    duration_s: int | None
    active: bool
    created_at: datetime

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Case:
        return cls(
            case_number=row["case_number"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            moderator_id=row["moderator_id"],
            action=row["action"],
            reason=row["reason"],
            duration_s=row["duration_s"],
            active=bool(row["active"]),
            created_at=from_db(row["created_at"]),
        )


@dataclass(slots=True)
class EscalationRule:
    guild_id: int
    threshold: int
    action: str
    duration_s: int | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> EscalationRule:
        return cls(
            guild_id=row["guild_id"],
            threshold=row["threshold"],
            action=row["action"],
            duration_s=row["duration_s"],
        )


@dataclass(slots=True)
class Job:
    id: int
    guild_id: int
    channel_id: int | None
    user_id: int
    kind: str
    payload: dict[str, Any]
    run_at: datetime
    attempts: int

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Job:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            user_id=row["user_id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            run_at=from_db(row["run_at"]),
            attempts=row["attempts"],
        )


class Database:
    """Wrapper fino sobre aiosqlite com as queries da aplicação."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() não foi chamado")
        return self._conn

    async def connect(self) -> None:
        # isolation_level=None (autocommit) precisa ir no connect(): o
        # aiosqlite mantém a conexão numa thread própria e atribuir a
        # propriedade depois violaria a checagem de thread do sqlite3.
        # Sem autocommit, o "BEGIN IMMEDIATE" de add_case() falharia com
        # "cannot start a transaction within a transaction".
        self._conn = await aiosqlite.connect(self._path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        # WAL permite leitura concorrente com escrita; foreign_keys não é
        # ligado por padrão no SQLite.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.commit()
        await self.migrate()
        log.info("Banco conectado: %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def backup(self, destino: Path) -> int:
        """Backup online consistente. Devolve o tamanho em bytes.

        Copiar o arquivo .db com o bot rodando é corrupção esperando
        acontecer: com WAL ativo, parte dos dados ainda vive no -wal e o
        snapshot sairia incompleto. A API de backup do SQLite lê as páginas
        sob lock, com o banco em uso.

        A escrita vai para um .partial renomeado no fim: um backup
        interrompido nunca deixa um .db truncado com cara de válido.
        """
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_suffix(".partial")
        parcial.unlink(missing_ok=True)
        try:
            async with aiosqlite.connect(parcial) as alvo:
                await self.conn.backup(alvo)
            parcial.replace(destino)
        finally:
            parcial.unlink(missing_ok=True)
        # ASYNC240 alerta sobre pathlib em corrotina, mas stat() é leitura de
        # metadado (microssegundos). O trabalho pesado — copiar as páginas —
        # já acontece na thread do aiosqlite.
        return destino.stat().st_size  # noqa: ASYNC240

    async def migrate(self) -> None:
        """Aplica os .sql de migrations/ em ordem, uma única vez cada."""
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        await self.conn.commit()

        cursor = await self.conn.execute("SELECT name FROM _migrations")
        applied = {row["name"] for row in await cursor.fetchall()}

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            log.info("Aplicando migração %s", sql_file.name)
            await self.conn.executescript(sql_file.read_text(encoding="utf-8"))
            await self.conn.execute(
                "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
                (sql_file.name, to_db(now())),
            )
            await self.conn.commit()

    # ------------------------------------------------------------------
    # Configuração por guild
    # ------------------------------------------------------------------

    async def get_config(self, guild_id: int) -> GuildConfig:
        """Retorna a config da guild, criando a linha padrão se não existir."""
        cursor = await self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        )
        row = await cursor.fetchone()
        if row is not None:
            return GuildConfig.from_row(row)

        stamp = to_db(now())
        await self.conn.execute(
            "INSERT INTO guild_config (guild_id, created_at, updated_at) VALUES (?, ?, ?)",
            (guild_id, stamp, stamp),
        )
        await self.conn.commit()
        return GuildConfig(guild_id=guild_id)

    async def set_config(self, guild_id: int, **fields: Any) -> None:
        """Atualiza campos da config. Nomes de coluna são validados por allow-list."""
        allowed = {
            "log_channel_id",
            "alert_channel_id",
            "welcome_channel_id",
            "welcome_enabled",
            "autorole_id",
            "min_account_age_days",
            "staff_role_ids",
            "raid_joins",
            "raid_window_s",
            "raid_action",
            "raid_lockdown_minutes",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Campos de config desconhecidos: {sorted(unknown)}")
        if not fields:
            return

        await self.get_config(guild_id)  # garante que a linha existe

        if isinstance(fields.get("staff_role_ids"), list):
            fields["staff_role_ids"] = json.dumps(fields["staff_role_ids"])
        if isinstance(fields.get("welcome_enabled"), bool):
            fields["welcome_enabled"] = int(fields["welcome_enabled"])

        # Os nomes vêm da allow-list acima; os VALORES seguem parametrizados.
        assignments = ", ".join(f"{name} = ?" for name in fields)
        await self.conn.execute(
            f"UPDATE guild_config SET {assignments}, updated_at = ? WHERE guild_id = ?",
            (*fields.values(), to_db(now()), guild_id),
        )
        await self.conn.commit()

    # ------------------------------------------------------------------
    # Casos de moderação
    # ------------------------------------------------------------------

    async def add_case(
        self,
        *,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        duration_s: int | None = None,
        automatic: bool = False,
    ) -> int:
        """Registra um caso e devolve o número sequencial dele na guild.

        `automatic=True` marca punições geradas pelo escalonamento: elas
        ficam no histórico, mas não contam para o próximo limiar.
        """
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.conn.execute(
                "SELECT COALESCE(MAX(case_number), 0) + 1 AS n FROM cases WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            number = row["n"]
            await self.conn.execute(
                "INSERT INTO cases (guild_id, case_number, user_id, moderator_id,"
                " action, reason, duration_s, automatic, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guild_id,
                    number,
                    user_id,
                    moderator_id,
                    action,
                    reason,
                    duration_s,
                    int(automatic),
                    to_db(now()),
                ),
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        return number

    async def get_case(self, guild_id: int, case_number: int) -> Case | None:
        cursor = await self.conn.execute(
            "SELECT * FROM cases WHERE guild_id = ? AND case_number = ?",
            (guild_id, case_number),
        )
        row = await cursor.fetchone()
        return Case.from_row(row) if row else None

    async def get_user_cases(self, guild_id: int, user_id: int, limit: int = 10) -> list[Case]:
        cursor = await self.conn.execute(
            "SELECT * FROM cases WHERE guild_id = ? AND user_id = ?"
            " ORDER BY case_number DESC LIMIT ?",
            (guild_id, user_id, limit),
        )
        return [Case.from_row(row) for row in await cursor.fetchall()]

    async def count_active_cases(self, guild_id: int, user_id: int, within_days: int = 30) -> int:
        """Infrações ativas na janela — base para o escalonamento.

        Exclui `automatic = 1`: a punição que o escalonamento aplicou não é
        uma nova infração do usuário, e contá-la faria a régua andar sozinha.
        """
        cutoff = to_db(now() - timedelta(days=within_days))
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM cases"
            " WHERE guild_id = ? AND user_id = ? AND active = 1"
            "   AND automatic = 0 AND created_at >= ?",
            (guild_id, user_id, cutoff),
        )
        row = await cursor.fetchone()
        return row["n"]

    # ------------------------------------------------------------------
    # Regras de escalonamento
    # ------------------------------------------------------------------

    async def get_escalation_rules(self, guild_id: int) -> list[EscalationRule]:
        cursor = await self.conn.execute(
            "SELECT * FROM escalation_rules WHERE guild_id = ? ORDER BY threshold",
            (guild_id,),
        )
        return [EscalationRule.from_row(row) for row in await cursor.fetchall()]

    async def set_escalation_rule(
        self, guild_id: int, threshold: int, action: str, duration_s: int | None
    ) -> None:
        """Cria ou substitui a regra daquele limiar."""
        await self.conn.execute(
            "INSERT INTO escalation_rules (guild_id, threshold, action, duration_s,"
            " created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (guild_id, threshold) DO UPDATE SET"
            "   action = excluded.action, duration_s = excluded.duration_s",
            (guild_id, threshold, action, duration_s, to_db(now())),
        )
        await self.conn.commit()

    async def delete_escalation_rule(self, guild_id: int, threshold: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM escalation_rules WHERE guild_id = ? AND threshold = ?",
            (guild_id, threshold),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Fila de agendamentos
    # ------------------------------------------------------------------

    async def schedule(
        self,
        *,
        guild_id: int,
        user_id: int,
        kind: str,
        run_at: datetime,
        channel_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        cursor = await self.conn.execute(
            "INSERT INTO schedules (guild_id, channel_id, user_id, kind, payload,"
            " run_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                channel_id,
                user_id,
                kind,
                json.dumps(payload or {}),
                to_db(run_at),
                to_db(now()),
            ),
        )
        await self.conn.commit()
        return cursor.lastrowid or 0

    async def claim_due_jobs(self, limit: int = 50) -> list[Job]:
        """Reivindica atomicamente os jobs vencidos.

        A marcação `claimed_at` impede que o mesmo job rode duas vezes se o
        loop demorar mais que o intervalo. Jobs reivindicados há mais de
        CLAIM_TTL são liberados — significa que o processo caiu no meio.
        """
        stamp = now()
        cursor = await self.conn.execute(
            "UPDATE schedules SET claimed_at = ? WHERE id IN ("
            "  SELECT id FROM schedules"
            "   WHERE done_at IS NULL AND run_at <= ?"
            "     AND (claimed_at IS NULL OR claimed_at < ?)"
            "   ORDER BY run_at LIMIT ?"
            ") RETURNING *",
            (to_db(stamp), to_db(stamp), to_db(stamp - CLAIM_TTL), limit),
        )
        rows = await cursor.fetchall()
        await self.conn.commit()
        return [Job.from_row(row) for row in rows]

    async def get_pending_job(self, guild_id: int, kind: str) -> Job | None:
        """Job pendente mais próximo daquele tipo — usado para desfazer algo
        antes da hora (ex.: liberar um bloqueio anti-raid manualmente)."""
        cursor = await self.conn.execute(
            "SELECT * FROM schedules WHERE guild_id = ? AND kind = ?"
            "   AND done_at IS NULL ORDER BY run_at LIMIT 1",
            (guild_id, kind),
        )
        row = await cursor.fetchone()
        return Job.from_row(row) if row else None

    async def mark_done(self, job_id: int) -> None:
        await self.conn.execute(
            "UPDATE schedules SET done_at = ? WHERE id = ?", (to_db(now()), job_id)
        )
        await self.conn.commit()

    async def reschedule_with_backoff(self, job_id: int, attempts: int, error: str) -> None:
        """Backoff exponencial limitado a 1h; desiste após 5 tentativas."""
        attempts += 1
        if attempts >= 5:
            await self.conn.execute(
                "UPDATE schedules SET done_at = ?, attempts = ?, last_error = ? WHERE id = ?",
                (to_db(now()), attempts, error[:500], job_id),
            )
        else:
            delay = min(timedelta(seconds=30 * 2**attempts), timedelta(hours=1))
            await self.conn.execute(
                "UPDATE schedules SET run_at = ?, claimed_at = NULL, attempts = ?,"
                " last_error = ? WHERE id = ?",
                (to_db(now() + delay), attempts, error[:500], job_id),
            )
        await self.conn.commit()

    async def cancel_jobs(self, *, guild_id: int, user_id: int, kind: str) -> int:
        """Cancela jobs pendentes de um tipo (ex.: unban após unban manual)."""
        cursor = await self.conn.execute(
            "UPDATE schedules SET done_at = ?"
            " WHERE guild_id = ? AND user_id = ? AND kind = ? AND done_at IS NULL",
            (to_db(now()), guild_id, user_id, kind),
        )
        await self.conn.commit()
        return cursor.rowcount
