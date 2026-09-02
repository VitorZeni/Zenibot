-- Sistema de tickets: canal privado por atendimento.

ALTER TABLE guild_config ADD COLUMN ticket_category_id INTEGER;

-- numero é sequencial POR GUILD, como os casos de moderação: o ticket #7 de
-- um servidor não tem relação com o #7 de outro.
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    numero      INTEGER NOT NULL,
    channel_id  INTEGER,            -- NULL enquanto o canal não existe ou após apagar
    opener_id   INTEGER NOT NULL,
    claimed_by  INTEGER,
    assunto     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'aberto',  -- aberto | fechado
    created_at  TEXT    NOT NULL,
    closed_at   TEXT,
    UNIQUE (guild_id, numero)
);

-- Índice parcial: a consulta quente é "esta pessoa já tem ticket aberto?",
-- feita a cada clique no botão do painel.
CREATE INDEX IF NOT EXISTS idx_tickets_abertos
    ON tickets (guild_id, opener_id) WHERE status = 'aberto';

CREATE INDEX IF NOT EXISTS idx_tickets_canal ON tickets (channel_id);
