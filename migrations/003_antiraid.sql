-- Anti-raid: detecção por janela deslizante de entradas.

-- raid_joins = 0 desativa. O padrão é 'alert' porque bloquear o servidor
-- exige a permissão Manage Server, que o convite recomendado não concede —
-- alertar funciona sem permissão extra e nunca prejudica membros legítimos.
ALTER TABLE guild_config ADD COLUMN raid_joins INTEGER NOT NULL DEFAULT 0;
ALTER TABLE guild_config ADD COLUMN raid_window_s INTEGER NOT NULL DEFAULT 60;
ALTER TABLE guild_config ADD COLUMN raid_action TEXT NOT NULL DEFAULT 'alert';
ALTER TABLE guild_config ADD COLUMN raid_lockdown_minutes INTEGER NOT NULL DEFAULT 15;
