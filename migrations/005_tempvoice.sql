-- Canais de voz temporários (entrar num saguão cria o seu).

ALTER TABLE guild_config ADD COLUMN voice_lobby_id INTEGER;
ALTER TABLE guild_config ADD COLUMN voice_category_id INTEGER;
ALTER TABLE guild_config ADD COLUMN voice_user_limit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN voice_max_channels INTEGER NOT NULL DEFAULT 20;

-- Sem esta tabela não haveria como saber quais canais são nossos depois de um
-- restart, e um bot que caiu deixaria canais órfãos para sempre. A varredura
-- de inicialização depende dela.
CREATE TABLE IF NOT EXISTS temp_voice_channels (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    owner_id   INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tempvoice_guild
    ON temp_voice_channels (guild_id, owner_id);
