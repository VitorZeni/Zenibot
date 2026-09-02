"""Callbacks de componente: a lógica que só rodava no servidor.

O `dpytest` não cobre interação nenhuma, então até aqui botões e selects só
eram exercitados clicando no Discord. Estes testes usam banco real e Discord
falso (ver `fakes.py`), de forma que a lógica é a de produção e o que se
observa é o que o usuário teria recebido.
"""

from __future__ import annotations

import discord
import pytest

from tests.fakes import FakeChannel, FakeGuild, FakeInteraction, FakeMember, FakeRole
from zenibot.bot import Zenibot
from zenibot.cogs import selfroles
from zenibot.cogs.party import PartyButton
from zenibot.cogs.selfroles import RoleToggleButton
from zenibot.cogs.tickets import TicketButton


@pytest.fixture(autouse=True)
def cooldown_limpo():
    """O cooldown dos self-roles é estado global do módulo. Sem limpar, um
    teste rate-limita o seguinte e a falha aparece longe da causa."""
    selfroles._cooldown.clear()
    yield
    selfroles._cooldown.clear()

# ---------------------------------------------------------------------------
# Self-roles: o botão de alternar cargo
# ---------------------------------------------------------------------------


def cenario_selfrole(*, cargo_perms=None, posicao=10, tem_cargo=False):
    """Guild com um cargo alvo e um membro que talvez já o tenha."""
    cargo = FakeRole(500, "Notificações", posicao, **(cargo_perms or {}))
    guild = FakeGuild(roles=[cargo])
    membro = FakeMember(100, "pessoa", roles=[cargo] if tem_cargo else [])
    membro.guild = guild
    return guild, membro, cargo


async def test_clique_concede_o_cargo() -> None:
    guild, membro, cargo = cenario_selfrole()
    interaction = FakeInteraction(guild=guild, user=membro)

    await RoleToggleButton(cargo.id, label="Notificações").toggle(interaction)

    assert cargo in membro.concedidos
    assert "adicionado" in interaction.texto


async def test_clique_repetido_remove_o_cargo() -> None:
    guild, membro, cargo = cenario_selfrole(tem_cargo=True)
    interaction = FakeInteraction(guild=guild, user=membro)

    await RoleToggleButton(cargo.id, label="Notificações").toggle(interaction)

    assert cargo in membro.removidos
    assert "removido" in interaction.texto


async def test_cargo_apagado_avisa_em_vez_de_estourar() -> None:
    guild = FakeGuild()  # sem o cargo
    interaction = FakeInteraction(guild=guild, user=FakeMember())

    await RoleToggleButton(999, label="Sumiu").callback(interaction)

    assert interaction.respondeu, "o botão não respondeu à interação"
    assert "não existe mais" in interaction.texto


async def test_cargo_que_ganhou_permissao_perigosa_e_bloqueado() -> None:
    """Revalidação no clique: o cargo pode ter virado administrativo depois
    de o painel ter sido criado."""
    guild, membro, cargo = cenario_selfrole(cargo_perms={"administrator": True})
    interaction = FakeInteraction(guild=guild, user=membro)

    await RoleToggleButton(cargo.id, label="Perigoso").callback(interaction)

    assert membro.concedidos == []
    assert "administrativas" in interaction.texto


async def test_cargo_acima_do_bot_e_recusado() -> None:
    """O cargo do bot está na posição 100; este está acima."""
    guild, membro, cargo = cenario_selfrole(posicao=200)
    interaction = FakeInteraction(guild=guild, user=membro)

    await RoleToggleButton(cargo.id, label="Alto").callback(interaction)

    assert membro.concedidos == []
    assert "hierarquia" in interaction.texto


async def test_fora_de_guild_responde_com_erro() -> None:
    interaction = FakeInteraction()
    interaction.guild = None

    await RoleToggleButton(1, label="x").callback(interaction)

    assert interaction.respondeu
    assert "servidor" in interaction.texto


async def test_cliques_em_rajada_caem_no_cooldown() -> None:
    """Sem o cooldown, spam no botão enfileira requisições de cargo e queima
    o rate limit do bot inteiro."""
    guild, membro, cargo = cenario_selfrole()
    botao = RoleToggleButton(cargo.id, label="Notificações")

    respostas = []
    for _ in range(8):
        interaction = FakeInteraction(guild=guild, user=membro)
        await botao.callback(interaction)
        respostas.append(interaction.texto)

    assert any("Devagar" in r for r in respostas)


# ---------------------------------------------------------------------------
# Tickets: guardas dos botões
# ---------------------------------------------------------------------------


async def test_assumir_exige_staff(bot: Zenibot) -> None:
    guild = FakeGuild()
    ticket_id, _ = await bot.db.open_ticket(
        guild_id=guild.id, opener_id=1, assunto="x"
    )
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(100))

    await TicketButton("assumir", ticket_id).callback(interaction)

    assert "equipe" in interaction.texto
    assert (await bot.db.get_ticket(ticket_id)).claimed_by is None


async def test_staff_assume_e_o_segundo_e_recusado(bot: Zenibot) -> None:
    """A exclusividade é do banco; aqui se verifica que ela chega ao usuário."""
    guild = FakeGuild()
    ticket_id, _ = await bot.db.open_ticket(
        guild_id=guild.id, opener_id=1, assunto="x"
    )
    primeiro = FakeMember(100, "mod1", manage_guild=True)
    segundo = FakeMember(200, "mod2", manage_guild=True)

    i1 = FakeInteraction(client=bot, guild=guild, user=primeiro)
    await TicketButton("assumir", ticket_id).callback(i1)
    assert (await bot.db.get_ticket(ticket_id)).claimed_by == 100

    i2 = FakeInteraction(client=bot, guild=guild, user=segundo)
    await TicketButton("assumir", ticket_id).callback(i2)
    assert "já assumiu" in i2.texto
    assert (await bot.db.get_ticket(ticket_id)).claimed_by == 100


async def test_terceiro_nao_fecha_ticket_alheio(bot: Zenibot) -> None:
    guild = FakeGuild()
    ticket_id, _ = await bot.db.open_ticket(
        guild_id=guild.id, opener_id=1, assunto="x"
    )
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(777))

    await TicketButton("fechar", ticket_id).callback(interaction)

    assert "quem abriu" in interaction.texto
    assert (await bot.db.get_ticket(ticket_id)).status == "aberto"


async def test_ticket_inexistente_avisa(bot: Zenibot) -> None:
    interaction = FakeInteraction(
        client=bot, guild=FakeGuild(), user=FakeMember(manage_guild=True)
    )
    await TicketButton("assumir", 9999).callback(interaction)
    assert "não existe mais" in interaction.texto


# ---------------------------------------------------------------------------
# Party: entrar, trocar e lotação
# ---------------------------------------------------------------------------


async def cria_grupo(bot: Zenibot, guild_id: int = 42, **vagas) -> int:
    return await bot.db.create_party(
        guild_id=guild_id,
        owner_id=1,
        titulo="Raide",
        descricao="",
        inicio=None,
        vagas=vagas or {"tank": 1, "dps": 2},
    )


async def test_entrar_no_grupo_pelo_botao(bot: Zenibot) -> None:
    guild = FakeGuild()
    pid = await cria_grupo(bot, guild.id)
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(100))

    await PartyButton("entrar", pid, "tank").callback(interaction)

    assert await bot.db.party_role_of(pid, 100) == "tank"
    # O painel é redesenhado, não respondido com mensagem nova.
    assert interaction.response.editado is not None


async def test_funcao_lotada_avisa_e_preserva_a_vaga(bot: Zenibot) -> None:
    guild = FakeGuild()
    pid = await cria_grupo(bot, guild.id, tank=1, dps=1)

    for uid, funcao in ((100, "tank"), (200, "dps")):
        i = FakeInteraction(client=bot, guild=guild, user=FakeMember(uid))
        await PartyButton("entrar", pid, funcao).callback(i)

    # 100 tenta trocar para dps, que está cheia.
    i = FakeInteraction(client=bot, guild=guild, user=FakeMember(100))
    await PartyButton("entrar", pid, "dps").callback(i)

    assert "acabaram de encher" in i.texto
    assert await bot.db.party_role_of(pid, 100) == "tank"


async def test_grupo_encerrado_recusa_entrada(bot: Zenibot) -> None:
    guild = FakeGuild()
    pid = await cria_grupo(bot, guild.id)
    await bot.db.close_party(pid)

    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(100))
    await PartyButton("entrar", pid, "tank").callback(interaction)

    assert "encerrado" in interaction.texto
    assert await bot.db.party_role_of(pid, 100) is None


async def test_so_o_dono_encerra(bot: Zenibot) -> None:
    guild = FakeGuild()
    pid = await bot.db.create_party(
        guild_id=guild.id, owner_id=1, titulo="R", descricao="",
        inicio=None, vagas={"tank": 1},
    )
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(777))

    await PartyButton("encerrar", pid).callback(interaction)

    assert "organizou" in interaction.texto
    assert (await bot.db.get_party(pid)).status == "aberta"


# ---------------------------------------------------------------------------
# Nenhum callback pode deixar a interação sem resposta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("botao", "descricao"),
    [
        (lambda: RoleToggleButton(9999, label="x"), "self-role com cargo apagado"),
        (lambda: TicketButton("assumir", 9999), "ticket inexistente"),
        (lambda: PartyButton("entrar", 9999, "tank"), "grupo inexistente"),
    ],
)
async def test_falha_sempre_vira_mensagem(bot: Zenibot, botao, descricao: str) -> None:
    """Regressão do bug mais custoso da sessão: `View.on_error` do discord.py
    só registra no log, então uma exceção deixava o usuário vendo "o
    aplicativo não respondeu a tempo", sem explicação."""
    interaction = FakeInteraction(
        client=bot, guild=FakeGuild(), user=FakeMember(manage_guild=True)
    )

    await botao().callback(interaction)

    assert interaction.respondeu, f"{descricao} não respondeu à interação"
    assert interaction.texto, f"{descricao} respondeu sem explicação"


def test_dublê_de_cargo_compara_por_hierarquia() -> None:
    """A comparação é o que sustenta as checagens de hierarquia nos testes."""
    baixo, alto = FakeRole(1, "baixo", 1), FakeRole(2, "alto", 10)
    assert alto >= baixo
    assert not (baixo >= alto)


def test_dublê_de_canal_registra_envios() -> None:
    canal = FakeChannel()
    assert canal.permissions_for(None).send_messages is True
    assert canal.enviadas == []


def test_dublê_de_membro_reflete_cargos_concedidos() -> None:
    membro = FakeMember()
    cargo = FakeRole(1, "Novo", 5)
    assert membro.top_role.position == 0
    membro.roles.append(cargo)
    assert membro.top_role is cargo


def test_permissoes_do_dublê_sao_reais() -> None:
    membro = FakeMember(manage_guild=True)
    assert isinstance(membro.guild_permissions, discord.Permissions)
    assert membro.guild_permissions.manage_guild is True
    assert membro.guild_permissions.administrator is False
