"""Fixtures compartilhadas.

Nenhum teste toca a rede ou o Discord: tudo roda contra um banco temporário
e credenciais falsas.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from zenibot.bot import Zenibot
from zenibot.config import Settings, load_settings
from zenibot.core.db import Database


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Configuração isolada, com banco próprio por teste.

    As variáveis de ambiente têm precedência sobre o .env no
    pydantic-settings, então o token real do projeto nunca é usado aqui.
    """
    monkeypatch.setenv("ZENIBOT_TOKEN", "x" * 60)
    monkeypatch.setenv("ZENIBOT_OWNER_IDS", "111,222")
    monkeypatch.setenv("ZENIBOT_DEV_GUILD_ID", "999")
    monkeypatch.setenv("ZENIBOT_DB_PATH", str(tmp_path / "zenibot.db"))
    monkeypatch.setenv("ZENIBOT_LOG_LEVEL", "CRITICAL")
    monkeypatch.setenv("ZENIBOT_BACKUP_INTERVAL_HOURS", "24")
    monkeypatch.setenv("ZENIBOT_BACKUP_KEEP", "7")
    return load_settings()


@pytest_asyncio.fixture
async def db(settings: Settings) -> Database:
    """Banco migrado e pronto."""
    database = Database(settings.db_file)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def bot(settings: Settings) -> Zenibot:
    """Bot com cogs carregados, sem Gateway e sem tarefas de fundo."""
    instancia = Zenibot(settings, background_tasks=False)
    await instancia.__aenter__()
    await instancia.setup_hook()
    try:
        yield instancia
    finally:
        await instancia.close()
