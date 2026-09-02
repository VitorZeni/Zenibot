"""Escolha de cor: paleta visual, nomes em português e hex.

O campo hex sozinho exige saber o código de antemão, e a única forma de
conferir é digitar e olhar o resultado — escolher uma cor vira exercício de
memória em vez de reconhecimento.

A paleta resolve isso com quadrados de emoji, que o Discord renderiza como
cor de verdade dentro do menu. O hex continua disponível para quem precisa
de um tom exato, e o texto também aceita nomes.
"""

from __future__ import annotations

import random
import re

import discord
from discord import ui

# Quadrados de emoji padrão (Unicode 12+), que renderizam em qualquer
# cliente. Evita emojis recentes como 🩷 e 🩵, que aparecem como caixa
# vazia em sistemas mais antigos.
PALETA: list[tuple[str, str, int]] = [
    ("Blurple", "🟦", 0x5865F2),
    ("Verde", "🟩", 0x57F287),
    ("Amarelo", "🟨", 0xFEE75C),
    ("Laranja", "🟧", 0xE67E22),
    ("Vermelho", "🟥", 0xED4245),
    ("Roxo", "🟪", 0x9B59B6),
    ("Marrom", "🟫", 0x8B5A2B),
    ("Cinza", "⬜", 0x99AAB5),
    ("Escuro", "⬛", 0x2B2D31),
]

# Aceitos no campo de texto, além do hex.
NOMEADAS: dict[str, int] = {
    "blurple": 0x5865F2,
    "verde": 0x57F287,
    "amarelo": 0xFEE75C,
    "laranja": 0xE67E22,
    "vermelho": 0xED4245,
    "roxo": 0x9B59B6,
    "marrom": 0x8B5A2B,
    "cinza": 0x99AAB5,
    "escuro": 0x2B2D31,
    "azul": 0x3498DB,
    "rosa": 0xEB459E,
    "preto": 0x000000,
    "branco": 0xFFFFFF,
}

# Valores especiais do menu, resolvidos em tempo de clique.
CARGO = "cargo"
ALEATORIA = "aleatoria"
HEX = "hex"
NENHUMA = "nenhuma"

INVALIDO = re.compile(r"[^0-9a-fA-F]")


def parse_cor(valor: str) -> discord.Colour | None:
    """Aceita `#5865F2`, `5865F2`, `#58F`, `0x5865F2`, `rgb(...)` e nomes.

    Vazio devolve None (sem cor). Levanta ValueError no formato inválido.
    """
    valor = valor.strip()
    if not valor:
        return None

    nomeada = NOMEADAS.get(valor.lower())
    if nomeada is not None:
        return discord.Colour(nomeada)

    if not valor.startswith(("#", "0x", "rgb")):
        valor = f"#{valor}"

    # O from_str do discord.py completa hex incompleto com zero à esquerda:
    # "#12345" vira "#012345" sem reclamar. Como isso transforma um erro de
    # digitação numa cor errada em silêncio, exigimos 3 ou 6 dígitos.
    if valor.startswith("#") and len(valor) - 1 not in (3, 6):
        raise ValueError(f"hex precisa ter 3 ou 6 dígitos: {valor}")

    return discord.Colour.from_str(valor)


def opcoes(atual: discord.Colour | None = None) -> list[discord.SelectOption]:
    """Opções do menu, com a cor atual marcada."""
    itens = [
        discord.SelectOption(
            label=nome,
            value=f"{valor:06x}",
            emoji=emoji,
            default=atual is not None and atual.value == valor,
        )
        for nome, emoji, valor in PALETA
    ]
    itens += [
        discord.SelectOption(
            label="Cor do meu cargo", value=CARGO, emoji="🎨",
            description="Usa a cor do seu cargo mais alto",
        ),
        discord.SelectOption(
            label="Aleatória", value=ALEATORIA, emoji="🎲",
        ),
        discord.SelectOption(
            label="Personalizada (hex)", value=HEX, emoji="✏️",
            description="Digite um código como #5865F2",
        ),
        discord.SelectOption(
            label="Sem cor", value=NENHUMA, emoji="🚫",
            default=atual is None,
        ),
    ]
    return itens


def resolver(escolha: str, membro: discord.Member) -> discord.Colour | None:
    """Traduz o valor escolhido no menu para uma cor.

    `HEX` não é resolvido aqui: ele exige abrir um modal, o que é
    responsabilidade da interface.
    """
    if escolha == NENHUMA:
        return None
    if escolha == ALEATORIA:
        return discord.Colour(random.randint(0, 0xFFFFFF))
    if escolha == CARGO:
        cor = membro.colour
        # Cor 0 significa "sem cor" no Discord: cair no Blurple é melhor que
        # devolver preto, que o usuário não pediu.
        return cor if cor.value else discord.Colour(0x5865F2)
    return discord.Colour(int(escolha, 16))


class CorSelect(ui.Select):
    """Menu de cor reutilizável.

    Subclasses implementam `aplicar` (guardar a cor escolhida e redesenhar) e
    `pedir_hex` (abrir o modal de código personalizado).
    """

    def __init__(self, atual: discord.Colour | None = None) -> None:
        super().__init__(
            placeholder="Cor da faixa",
            options=opcoes(atual),
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        escolha = self.values[0]
        if escolha == HEX:
            await self.pedir_hex(interaction)
            return
        await self.aplicar(interaction, resolver(escolha, interaction.user))

    async def aplicar(
        self, interaction: discord.Interaction, cor: discord.Colour | None
    ) -> None:
        raise NotImplementedError

    async def pedir_hex(self, interaction: discord.Interaction) -> None:
        raise NotImplementedError
