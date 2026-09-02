"""Voz temporária: registro dos canais e limpeza de órfãos.

O que mais importa aqui é não deixar lixo para trás — canal órfão de um
restart, ou uma fila de canais com o mesmo dono.
"""

from __future__ import annotations

from zenibot.cogs.tempvoice import MAX_POR_CATEGORIA, nome_do_canal
from zenibot.core.db import Database

# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------


async def test_registra_e_reconhece_canal_temporario(db: Database) -> None:
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    assert await db.is_temp_voice(100) is True
    assert await db.is_temp_voice(999) is False


async def test_remove_registro(db: Database) -> None:
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    assert await db.remove_temp_voice(100) is True
    assert await db.remove_temp_voice(100) is False
    assert await db.is_temp_voice(100) is False


async def test_encontra_canal_pelo_dono(db: Database) -> None:
    """É o que evita virar fila de canais quando alguém entra e sai do
    saguão em sequência."""
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    assert await db.temp_voice_of_owner(42, 7) == 100
    assert await db.temp_voice_of_owner(42, 8) is None


async def test_dono_isolado_por_guild(db: Database) -> None:
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    assert await db.temp_voice_of_owner(43, 7) is None


async def test_contagem_por_guild(db: Database) -> None:
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    await db.add_temp_voice(channel_id=101, guild_id=42, owner_id=8)
    await db.add_temp_voice(channel_id=102, guild_id=43, owner_id=9)
    assert await db.count_temp_voice(42) == 2
    assert await db.count_temp_voice(43) == 1


async def test_listagem_global_alimenta_a_varredura(db: Database) -> None:
    """A varredura de inicialização percorre todas as guilds de uma vez."""
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    await db.add_temp_voice(channel_id=102, guild_id=43, owner_id=9)

    todos = await db.list_temp_voice()
    assert sorted(todos) == [(100, 42), (102, 43)]
    assert await db.list_temp_voice(43) == [(102, 43)]


async def test_reinserir_o_mesmo_canal_nao_duplica(db: Database) -> None:
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=7)
    await db.add_temp_voice(channel_id=100, guild_id=42, owner_id=8)
    assert await db.count_temp_voice(42) == 1
    assert await db.temp_voice_of_owner(42, 8) == 100


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


async def test_padroes_de_voz(db: Database) -> None:
    cfg = await db.get_config(42)
    assert cfg.voice_lobby_id is None
    assert cfg.voice_user_limit == 0
    assert cfg.voice_max_channels == 20


async def test_configuracao_persiste(db: Database) -> None:
    await db.set_config(
        42,
        voice_lobby_id=500,
        voice_category_id=600,
        voice_user_limit=5,
        voice_max_channels=10,
    )
    cfg = await db.get_config(42)
    assert (cfg.voice_lobby_id, cfg.voice_category_id) == (500, 600)
    assert (cfg.voice_user_limit, cfg.voice_max_channels) == (5, 10)


async def test_desativar_limpa_o_saguao(db: Database) -> None:
    await db.set_config(42, voice_lobby_id=500)
    await db.set_config(42, voice_lobby_id=None)
    assert (await db.get_config(42)).voice_lobby_id is None


# ---------------------------------------------------------------------------
# Detalhes
# ---------------------------------------------------------------------------


def test_nome_do_canal_respeita_o_limite_do_discord() -> None:
    class MembroFalso:
        display_name = "N" * 200

    assert len(nome_do_canal(MembroFalso())) <= 100


def test_teto_por_categoria_bate_com_o_do_discord() -> None:
    assert MAX_POR_CATEGORIA == 50
