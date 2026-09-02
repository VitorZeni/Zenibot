"""Boot do bot: configuração, intents, cogs, árvore de comandos, backup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zenibot.bot import INITIAL_COGS, Zenibot, build_intents
from zenibot.cogs.health import human_size
from zenibot.config import load_settings

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def test_settings_carrega_do_ambiente(settings) -> None:
    assert settings.dev_guild_id == 999
    assert settings.owner_ids == {111, 222}


def test_owner_ids_aceita_lista_separada_por_virgula(monkeypatch, tmp_path) -> None:
    """set[int] é tipo complexo: sem NoDecode, o pydantic-settings tentaria
    json.loads('111,222') antes do validador rodar."""
    monkeypatch.setenv("ZENIBOT_TOKEN", "x" * 60)
    monkeypatch.setenv("ZENIBOT_OWNER_IDS", "1, 2 ,3")
    monkeypatch.setenv("ZENIBOT_DB_PATH", str(tmp_path / "z.db"))
    assert load_settings().owner_ids == {1, 2, 3}


def test_token_curto_e_rejeitado(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZENIBOT_TOKEN", "curto")
    monkeypatch.setenv("ZENIBOT_DB_PATH", str(tmp_path / "z.db"))
    with pytest.raises(ValidationError):
        load_settings()


def test_backup_dir_acompanha_o_banco(settings) -> None:
    """Ficando ao lado do banco, os backups caem dentro do volume no Docker."""
    assert settings.backup_dir == settings.db_file.parent / "backups"


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------


def test_intents_privilegiados_necessarios() -> None:
    intents = build_intents()
    assert intents.members is True       # boas-vindas e autorole
    assert intents.moderation is True    # audit log e bans
    assert intents.guilds is True


def test_voice_states_ligado() -> None:
    """Sem ele on_voice_state_update nunca dispara e a voz temporária fica
    muda, sem erro nenhum. Não é privilegiado."""
    assert build_intents().voice_states is True


def test_message_content_permanece_desligado() -> None:
    """Decisão de desenho: os comandos são slash e a filtragem de conteúdo é
    do AutoMod nativo. Ligar isto exigiria revisar a política de privacidade."""
    assert build_intents().message_content is False


# ---------------------------------------------------------------------------
# Cogs e comandos
# ---------------------------------------------------------------------------


async def test_todos_os_cogs_carregam(bot: Zenibot) -> None:
    assert len(bot.cogs) == len(INITIAL_COGS)


async def test_arvore_de_comandos(bot: Zenibot) -> None:
    comandos = {c.qualified_name for c in bot.tree.walk_commands()}
    esperados = {
        "aviso", "silenciar", "expulsar", "banir", "desbanir", "caso", "historico",
        "ping", "sync", "reload", "lembrete", "backup",
        "config ver", "config canal", "config boas-vindas", "config autorole",
        "config idade-minima", "config staff",
        "painel criar", "painel adicionar", "painel remover",
        "escalonamento ver", "escalonamento definir", "escalonamento remover",
        "antiraid ver", "antiraid configurar", "antiraid liberar",
        "embed criar", "container criar",
        "modelo listar", "modelo usar", "modelo aplicar", "modelo apagar",
        "voz ver", "voz configurar", "voz desativar",
        "ticket configurar", "ticket painel", "ticket status",
        "canal criar", "canal acesso",
    }
    assert esperados <= comandos


async def test_nao_ha_comandos_com_nome_duplicado(bot: Zenibot) -> None:
    nomes = [c.qualified_name for c in bot.tree.walk_commands()]
    assert len(nomes) == len(set(nomes))


# ---------------------------------------------------------------------------
# Tarefas de fundo
# ---------------------------------------------------------------------------


async def test_scripts_nao_disparam_tarefas_de_fundo(bot: Zenibot) -> None:
    """Regressão: instanciar o bot num script utilitário gerava um backup, e
    com a retenção ativa isso expulsava backups legítimos da janela."""
    assert bot.get_cog("Health").backup_loop.is_running() is False
    assert bot.get_cog("Scheduler").process_jobs.is_running() is False


async def test_backup_sob_demanda_e_retencao(bot: Zenibot, settings) -> None:
    health = bot.get_cog("Health")
    nome, tamanho, _ = await health.run_backup()
    assert nome.startswith("zenibot-") and tamanho > 0

    pasta = settings.backup_dir
    for arquivo in pasta.glob("zenibot-*.db"):
        arquivo.unlink()
    for i in range(10):
        (pasta / f"zenibot-20260101-0000{i:02d}.db").write_bytes(b"x")

    podados = health.prune()
    restantes = sorted(p.name for p in pasta.glob("zenibot-*.db"))
    assert podados == 10 - settings.backup_keep
    assert len(restantes) == settings.backup_keep
    # O carimbo YYYYMMDD-HHMMSS faz a ordem alfabética coincidir com a
    # cronológica, então os descartados são mesmo os mais antigos.
    assert restantes[0] == "zenibot-20260101-000003.db"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(512, "512 B"), (2048, "2.0 KiB"), (5 * 1024 * 1024, "5.0 MiB")],
)
def test_human_size(valor: int, esperado: str) -> None:
    assert human_size(valor) == esperado
