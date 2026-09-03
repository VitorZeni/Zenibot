"""Validação para criar, editar e apagar cargos.

Mexer em cargo é operação sensível: é por cargo que se concede poder num
servidor. As checagens aqui existem para que o bot nunca vire atalho de
escalada de privilégio — nem por descuido de quem opera, nem por má-fé.

O cargo criado **não recebe permissão nenhuma**. Isso é deliberado: definir
permissões pela interface do Discord mantém as próprias travas dele no
caminho, e o valor do comando está em poupar o ritual de criar, colorir e
destacar — não em contornar a tela de permissões.
"""

from __future__ import annotations

import discord

from zenibot.core.checks import ZenibotError

# Teto do Discord por servidor.
MAX_CARGOS = 250
LIMITE_NOME = 100


def validar_nome(nome: str) -> str:
    nome = nome.strip()
    if not nome:
        raise ZenibotError("O cargo precisa de um nome.")
    if len(nome) > LIMITE_NOME:
        raise ZenibotError(f"O nome não pode passar de {LIMITE_NOME} caracteres.")
    if nome.lower() in ("@everyone", "everyone", "@here", "here"):
        raise ZenibotError("Esse nome é reservado pelo Discord.")
    return nome


def assert_pode_criar(guild: discord.Guild) -> None:
    if not guild.me.guild_permissions.manage_roles:
        raise ZenibotError("Preciso da permissão **Gerenciar Cargos**.")
    if len(guild.roles) >= MAX_CARGOS:
        raise ZenibotError(
            f"O servidor já tem {MAX_CARGOS} cargos, que é o teto do Discord. "
            "Apague algum antes de criar outro."
        )


def assert_gerenciavel(
    cargo: discord.Role, actor: discord.Member, me: discord.Member
) -> None:
    """Valida que este cargo pode ser editado ou apagado por esta pessoa."""
    if cargo.is_default():
        raise ZenibotError("O `@everyone` não pode ser editado por aqui.")
    if cargo.is_bot_managed() or cargo.is_integration():
        raise ZenibotError(
            f"{cargo.mention} é gerenciado por uma integração — quem o controla "
            "é o app dono dele, não eu."
        )
    if cargo.is_premium_subscriber():
        raise ZenibotError("O cargo de impulsionador é gerenciado pelo Discord.")

    if not me.guild_permissions.manage_roles:
        raise ZenibotError("Preciso da permissão **Gerenciar Cargos**.")
    if cargo >= me.top_role:
        raise ZenibotError(
            f"{cargo.mention} está no meu nível ou acima. Mova o cargo do "
            "Zenibot para cima na hierarquia."
        )

    # O dono do servidor não tem teto; qualquer outra pessoa só mexe no que
    # está abaixo dela. Sem isto, um moderador poderia renomear ou apagar o
    # cargo de administrador.
    if actor.id != actor.guild.owner_id and cargo >= actor.top_role:
        raise ZenibotError(
            f"{cargo.mention} está no seu nível ou acima dele. Você só pode "
            "mexer em cargos abaixo do seu."
        )


def descreve_permissoes(cargo: discord.Role) -> str:
    """Resumo legível do que o cargo concede, para confirmar antes de apagar."""
    if cargo.permissions.administrator:
        return "**Administrador** — todas as permissões"
    ativas = [nome for nome, ligada in cargo.permissions if ligada]
    if not ativas:
        return "nenhuma permissão de servidor"
    return f"{len(ativas)} permissão(ões) de servidor"
