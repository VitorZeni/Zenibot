"""Parsing de durações em texto: "10m", "2h30m", "7d"."""

from __future__ import annotations

import re
from datetime import timedelta

from zenibot.core.checks import ZenibotError

# Alternativas ordenadas da mais longa para a mais curta: sem isso "1sem"
# casaria com "s" (1 segundo) em vez de "sem" (1 semana).
_PATTERN = re.compile(
    r"(\d+)\s*(semanas?|sem|segundos?|seg|minutos?|min|horas?|hrs?|dias?|[smhdw])",
    re.IGNORECASE,
)

_UNITS = {
    "s": 1, "seg": 1, "segundo": 1, "segundos": 1,
    "m": 60, "min": 60, "minuto": 60, "minutos": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hora": 3600, "horas": 3600,
    "d": 86400, "dia": 86400, "dias": 86400,
    "w": 604800, "sem": 604800, "semana": 604800, "semanas": 604800,
}

# Limite do Discord para timeout de membro.
MAX_TIMEOUT = timedelta(days=28)


def parse_duration(text: str) -> timedelta:
    """Converte "1h30m" em timedelta. Levanta ZenibotError se inválido."""
    matches = _PATTERN.findall(text.strip())
    if not matches:
        raise ZenibotError(
            "Duração inválida. Use formatos como `30m`, `2h`, `1h30m` ou `7d`."
        )

    total = 0
    for amount, unit in matches:
        seconds = _UNITS.get(unit.lower())
        if seconds is None:
            raise ZenibotError(f"Unidade de tempo desconhecida: `{unit}`")
        total += int(amount) * seconds

    if total <= 0:
        raise ZenibotError("A duração precisa ser maior que zero.")
    return timedelta(seconds=total)


def humanize(delta: timedelta) -> str:
    """timedelta -> "1 dia, 3 horas" (pt-BR)."""
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} segundo(s)"

    partes: list[str] = []
    for unit_seconds, singular, plural in (
        (86400, "dia", "dias"),
        (3600, "hora", "horas"),
        (60, "minuto", "minutos"),
    ):
        value, seconds = divmod(seconds, unit_seconds)
        if value:
            partes.append(f"{value} {singular if value == 1 else plural}")
    return ", ".join(partes)
