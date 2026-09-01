"""Lógica pura: duração, redação de segredos, escalonamento, anti-raid,
segurança dos self-roles. Nada aqui precisa de banco ou de Discord."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace

import discord
import pytest

from zenibot.cogs.selfroles import RoleToggleButton, dangerous_permissions
from zenibot.core.antiraid import JoinWindow
from zenibot.core.checks import ZenibotError
from zenibot.core.db import EscalationRule
from zenibot.core.duration import humanize, parse_duration
from zenibot.core.escalation import describe, rule_for_count, validate
from zenibot.core.logging_setup import RedactSecrets

# ---------------------------------------------------------------------------
# Duração
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "segundos"),
    [
        ("30s", 30),
        ("30m", 1800),
        ("2h", 7200),
        ("1h30m", 5400),
        ("7d", 604800),
        # "1sem" precisa casar semana, não segundo: a alternância do regex é
        # ordenada da mais longa para a mais curta por causa disto.
        ("1sem", 604800),
        ("1w", 604800),
        ("2 dias", 172800),
    ],
)
def test_parse_duration(texto: str, segundos: int) -> None:
    assert parse_duration(texto).total_seconds() == segundos


@pytest.mark.parametrize("texto", ["banana", "", "0m", "-5m"])
def test_parse_duration_rejeita_invalido(texto: str) -> None:
    with pytest.raises(ZenibotError):
        parse_duration(texto)


def test_humanize() -> None:
    assert humanize(timedelta(minutes=90)) == "1 hora, 30 minutos"
    assert humanize(timedelta(days=1, hours=3)) == "1 dia, 3 horas"
    assert humanize(timedelta(seconds=45)) == "45 segundo(s)"


# ---------------------------------------------------------------------------
# Redação de segredos
# ---------------------------------------------------------------------------


def test_redact_token_no_log() -> None:
    # Montado em partes de propósito: escrito inteiro, o secret scanning do
    # GitHub bloquearia o push deste arquivo.
    token = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GaBcDe", "abcdefghijklmnopqrstuvwxyz123"])
    rec = logging.LogRecord("t", logging.INFO, "", 0, f"Bot {token}", None, None)
    RedactSecrets().filter(rec)
    assert "REDACTED" in rec.msg
    assert token not in rec.msg


def test_redact_preserva_texto_comum() -> None:
    rec = logging.LogRecord("t", logging.INFO, "", 0, "guild 42 conectada", None, None)
    RedactSecrets().filter(rec)
    assert rec.msg == "guild 42 conectada"


# ---------------------------------------------------------------------------
# Escalonamento
# ---------------------------------------------------------------------------


def regra(threshold: int, action: str = "timeout", duration_s: int | None = 3600):
    return EscalationRule(guild_id=1, threshold=threshold, action=action, duration_s=duration_s)


def test_regra_dispara_no_limiar_exato() -> None:
    regras = [regra(3), regra(5)]
    assert rule_for_count(regras, 3) is not None
    assert rule_for_count(regras, 5) is not None


def test_regra_nao_redispara_acima_do_limiar() -> None:
    """Com `>=`, a regra da 3ª puniria também na 4ª e na 5ª, sempre com a
    mesma pena — o oposto de escalonar."""
    regras = [regra(3)]
    assert rule_for_count(regras, 4) is None
    assert rule_for_count(regras, 10) is None


def test_regra_nao_dispara_abaixo_nem_sem_regras() -> None:
    assert rule_for_count([regra(3)], 2) is None
    assert rule_for_count([], 3) is None


@pytest.mark.parametrize(
    ("action", "duration_s", "valido"),
    [
        ("timeout", 3600, True),
        ("timeout", None, False),          # timeout exige duração
        ("timeout", 86400 * 40, False),    # acima dos 28 dias do Discord
        ("kick", None, True),
        ("kick", 3600, False),             # expulsão não tem duração
        ("ban", None, True),               # sem duração = permanente
        ("ban", 604800, True),
        ("banimento", None, False),        # ação inexistente
    ],
)
def test_validate(action: str, duration_s: int | None, valido: bool) -> None:
    if valido:
        validate(action, duration_s)
    else:
        with pytest.raises(ValueError):
            validate(action, duration_s)


def test_describe() -> None:
    assert describe(regra(3, "timeout", 3600)) == "timeout de 1 hora"
    assert describe(regra(3, "kick", None)) == "expulsão"
    assert describe(regra(3, "ban", None)) == "ban permanente"
    assert describe(regra(3, "ban", 604800)) == "ban de 7 dias"


# ---------------------------------------------------------------------------
# Anti-raid
# ---------------------------------------------------------------------------


def test_janela_conta_entradas_recentes() -> None:
    janela = JoinWindow()
    for i in range(5):
        janela.record(1, 100 + i, 1000.0 + i)
    assert len(janela.recent(1, 30, 1004.0)) == 5


def test_janela_descarta_entradas_antigas() -> None:
    janela = JoinWindow()
    for i in range(5):
        janela.record(1, 100 + i, 1000.0 + i)
    assert janela.recent(1, 3, 1010.0) == []


def test_janela_isola_guilds() -> None:
    janela = JoinWindow()
    janela.record(1, 11, 1000.0)
    janela.record(2, 22, 1000.0)
    assert janela.recent(1, 60, 1000.0) == [11]
    assert janela.recent(2, 60, 1000.0) == [22]


def test_janela_respeita_teto_por_guild() -> None:
    """Sem teto, um servidor movimentado faria a estrutura crescer sem fim."""
    janela = JoinWindow(max_por_guild=3)
    for i in range(10):
        janela.record(7, i, 1000.0 + i)
    assert len(janela.recent(7, 9999, 1009.0)) == 3


def test_janela_clear() -> None:
    janela = JoinWindow()
    janela.record(1, 11, 1000.0)
    janela.clear(1)
    assert janela.recent(1, 60, 1000.0) == []


# ---------------------------------------------------------------------------
# Self-roles
# ---------------------------------------------------------------------------


def cargo(**perms):
    return SimpleNamespace(permissions=discord.Permissions(**perms))


@pytest.mark.parametrize(
    "permissao",
    ["administrator", "manage_roles", "ban_members", "manage_webhooks", "manage_guild"],
)
def test_bloqueia_cargo_com_permissao_administrativa(permissao: str) -> None:
    """Um botão público que concede estes cargos é escalada de privilégio."""
    assert dangerous_permissions(cargo(**{permissao: True})) == [permissao]


def test_detecta_multiplas_permissoes_de_risco() -> None:
    achadas = dangerous_permissions(cargo(ban_members=True, manage_roles=True))
    assert set(achadas) == {"manage_roles", "ban_members"}


def test_libera_cargo_cosmetico() -> None:
    assert dangerous_permissions(cargo(send_messages=True, view_channel=True)) == []


def test_custom_id_do_botao_casa_o_template() -> None:
    """É o que faz um painel antigo voltar a responder após restart."""
    template = RoleToggleButton.__discord_ui_compiled_template__
    casou = template.fullmatch("srole:123456789012345678")
    assert casou is not None
    assert casou["role_id"] == "123456789012345678"


def test_template_ignora_outros_componentes() -> None:
    template = RoleToggleButton.__discord_ui_compiled_template__
    assert template.fullmatch("outro:123") is None
    assert template.fullmatch("srole:abc") is None


def test_botao_gera_custom_id_esperado() -> None:
    botao = RoleToggleButton(999, label="Notificações")
    assert botao.item.custom_id == "srole:999"
