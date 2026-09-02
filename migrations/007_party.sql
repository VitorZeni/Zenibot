-- Grupos com vagas por função (party / raide).

CREATE TABLE IF NOT EXISTS parties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER,
    message_id  INTEGER,
    owner_id    INTEGER NOT NULL,
    titulo      TEXT    NOT NULL,
    descricao   TEXT    NOT NULL DEFAULT '',
    inicio      TEXT,                -- NULL = sem horário marcado
    event_id    INTEGER,             -- Evento Agendado do Discord, se criado
    status      TEXT    NOT NULL DEFAULT 'aberta',  -- aberta | encerrada
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_parties_msg ON parties (message_id);

-- Uma linha por função ativa na party. Função ausente = não existe naquela
-- party, e não "capacidade zero".
CREATE TABLE IF NOT EXISTS party_slots (
    party_id   INTEGER NOT NULL,
    funcao     TEXT    NOT NULL,
    capacidade INTEGER NOT NULL,
    PRIMARY KEY (party_id, funcao)
);

-- A chave primária por (party, usuário) é o que garante uma vaga por pessoa:
-- entrar numa segunda função exige sair da primeira.
CREATE TABLE IF NOT EXISTS party_members (
    party_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    funcao    TEXT    NOT NULL,
    entrou_em TEXT    NOT NULL,
    PRIMARY KEY (party_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_party_members_funcao
    ON party_members (party_id, funcao);
