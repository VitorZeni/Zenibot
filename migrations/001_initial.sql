-- Migração inicial do Zenibot.
-- Timestamps são strings ISO-8601 em UTC com precisão de segundos
-- (largura fixa => a ordenação lexicográfica coincide com a cronológica).

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id              INTEGER PRIMARY KEY,
    log_channel_id        INTEGER,
    alert_channel_id      INTEGER,
    welcome_channel_id    INTEGER,
    welcome_enabled       INTEGER NOT NULL DEFAULT 0,
    autorole_id           INTEGER,
    min_account_age_days  INTEGER NOT NULL DEFAULT 0,
    staff_role_ids        TEXT    NOT NULL DEFAULT '[]',  -- JSON: lista de IDs
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
);

-- Casos de moderação. case_number é sequencial POR GUILD (o /case 42 do
-- servidor A é independente do /case 42 do servidor B).
CREATE TABLE IF NOT EXISTS cases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    case_number   INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    moderator_id  INTEGER NOT NULL,
    action        TEXT    NOT NULL,   -- warn | timeout | kick | ban | unban
    reason        TEXT    NOT NULL,
    duration_s    INTEGER,            -- NULL para ações sem duração
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    UNIQUE (guild_id, case_number)
);

CREATE INDEX IF NOT EXISTS idx_cases_user  ON cases (guild_id, user_id, created_at DESC);

-- Fila de agendamentos persistente. Sobrevive a restart: é isto que
-- diferencia um lembrete real de um asyncio.sleep().
CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER,
    user_id     INTEGER NOT NULL,
    kind        TEXT    NOT NULL,   -- reminder | unban | announce
    payload     TEXT    NOT NULL DEFAULT '{}',  -- JSON
    run_at      TEXT    NOT NULL,
    claimed_at  TEXT,
    done_at     TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT    NOT NULL
);

-- Índice parcial: o poller só varre pendentes, então o índice só os cobre.
CREATE INDEX IF NOT EXISTS idx_schedules_pending
    ON schedules (run_at) WHERE done_at IS NULL;
