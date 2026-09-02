"""Escolha de cor: paleta visual, nomes e hex.

O campo hex sozinho exige saber o código de antemão. A paleta existe para
que escolher uma cor seja reconhecimento, não memória — e estes testes
protegem as duas portas de entrada.
"""

from __future__ import annotations

import discord
import pytest

from zenibot.core import colors

# ---------------------------------------------------------------------------
# Hex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entrada", ["#5865F2", "5865F2", "0x5865F2", "#5865f2"])
def test_aceita_variacoes_de_hex(entrada: str) -> None:
    """O `#` é opcional porque quase todo mundo cola o hex sem ele."""
    assert colors.parse_cor(entrada) == discord.Colour(0x5865F2)


def test_aceita_atalho_de_tres_digitos() -> None:
    """`#123` expande para `#112233`, como no CSS."""
    assert colors.parse_cor("#123") == discord.Colour(0x112233)


def test_vazio_significa_sem_cor() -> None:
    assert colors.parse_cor("") is None
    assert colors.parse_cor("   ") is None


@pytest.mark.parametrize(
    "entrada",
    [
        "zzz",
        "#GGGGGG",
        # O from_str do discord.py completaria com zero à esquerda e devolveria
        # #012345 sem reclamar — um erro de digitação viraria cor errada.
        "#12345",
        "#1234",
        "#12",
    ],
)
def test_rejeita_hex_invalido(entrada: str) -> None:
    with pytest.raises(ValueError):
        colors.parse_cor(entrada)


# ---------------------------------------------------------------------------
# Nomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nome", "valor"),
    [("vermelho", 0xED4245), ("verde", 0x57F287), ("azul", 0x3498DB)],
)
def test_aceita_nome_em_portugues(nome: str, valor: int) -> None:
    assert colors.parse_cor(nome) == discord.Colour(valor)


def test_nome_ignora_maiusculas() -> None:
    assert colors.parse_cor("VERMELHO") == colors.parse_cor("vermelho")


def test_nome_desconhecido_nao_vira_hex_silenciosamente() -> None:
    with pytest.raises(ValueError):
        colors.parse_cor("turquesa")


# ---------------------------------------------------------------------------
# Paleta
# ---------------------------------------------------------------------------


def test_paleta_cabe_no_menu_do_discord() -> None:
    """Um select aceita no máximo 25 opções, contando as especiais."""
    assert len(colors.opcoes()) <= 25


def test_toda_cor_da_paleta_vira_opcao() -> None:
    valores = {o.value for o in colors.opcoes()}
    for _nome, _emoji, valor in colors.PALETA:
        assert f"{valor:06x}" in valores


def test_opcoes_tem_swatch_visual() -> None:
    """O quadrado de emoji é o que torna a escolha visual; sem ele o menu
    seria uma lista de nomes."""
    for opcao in colors.opcoes()[: len(colors.PALETA)]:
        assert opcao.emoji is not None


def test_cor_atual_aparece_marcada() -> None:
    marcadas = [o.value for o in colors.opcoes(discord.Colour(0x57F287)) if o.default]
    assert marcadas == ["57f287"]


def test_sem_cor_marcada_quando_nao_ha_cor() -> None:
    marcadas = [o.value for o in colors.opcoes(None) if o.default]
    assert marcadas == [colors.NENHUMA]


# ---------------------------------------------------------------------------
# Opções especiais
# ---------------------------------------------------------------------------


class MembroFalso:
    def __init__(self, cor: int) -> None:
        self.colour = discord.Colour(cor)


def test_resolve_cor_da_paleta() -> None:
    assert colors.resolver("57f287", MembroFalso(0)) == discord.Colour(0x57F287)


def test_resolve_sem_cor() -> None:
    assert colors.resolver(colors.NENHUMA, MembroFalso(0)) is None


def test_resolve_cor_do_cargo() -> None:
    assert colors.resolver(colors.CARGO, MembroFalso(0xABCDEF)) == discord.Colour(
        0xABCDEF
    )


def test_cargo_sem_cor_cai_no_blurple() -> None:
    """Cor 0 no Discord significa "sem cor"; devolver preto seria entregar
    algo que o usuário não pediu."""
    assert colors.resolver(colors.CARGO, MembroFalso(0)) == discord.Colour(0x5865F2)


def test_aleatoria_fica_no_intervalo_valido() -> None:
    for _ in range(50):
        cor = colors.resolver(colors.ALEATORIA, MembroFalso(0))
        assert 0 <= cor.value <= 0xFFFFFF
