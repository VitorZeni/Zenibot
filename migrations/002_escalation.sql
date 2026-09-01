-- Escalonamento automático de punições.

-- Punições aplicadas pelo próprio escalonamento são registradas para
-- auditoria, mas marcadas para NÃO contar no limiar: sem isto, a punição
-- gerada pela 3ª infração viraria a 4ª infração, e o próximo aviso dispararia
-- uma regra mais dura do que o merecido.
ALTER TABLE cases ADD COLUMN automatic INTEGER NOT NULL DEFAULT 0;

-- Uma regra por limiar, por servidor. A regra dispara quando a contagem de
-- infrações atinge EXATAMENTE o limiar — não a cada infração acima dele.
CREATE TABLE IF NOT EXISTS escalation_rules (
    guild_id   INTEGER NOT NULL,
    threshold  INTEGER NOT NULL,   -- nº de infrações que dispara a regra
    action     TEXT    NOT NULL,   -- timeout | kick | ban
    duration_s INTEGER,            -- NULL = permanente (ban) ou n/a (kick)
    created_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, threshold)
);
