-- Modelos de mensagem reutilizáveis.

-- payload guarda o suficiente para reconstruir a mensagem sem consultar o
-- Discord: o dict do embed, ou a lista de blocos e a cor do container. É
-- deliberado que a fonte da verdade seja esta tabela e não a mensagem
-- publicada — componentes V2 não voltam de forma confiável para o estado do
-- editor.
CREATE TABLE IF NOT EXISTS message_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    nome        TEXT    NOT NULL,
    tipo        TEXT    NOT NULL,   -- embed | container
    payload     TEXT    NOT NULL,   -- JSON
    criado_por  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (guild_id, nome)
);

CREATE INDEX IF NOT EXISTS idx_templates_guild
    ON message_templates (guild_id, nome);
