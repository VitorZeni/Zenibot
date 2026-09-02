"""Tickets: numeração, trava de duplicidade e concorrência ao assumir."""

from __future__ import annotations

import discord
import pytest

from zenibot.cogs.tickets import TicketButton, nome_do_canal, painel_view, ticket_view
from zenibot.core.db import Database, Ticket

# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


async def abrir(db: Database, guild_id: int = 42, opener_id: int = 7) -> tuple[int, int]:
    return await db.open_ticket(
        guild_id=guild_id, opener_id=opener_id, assunto="preciso de ajuda"
    )


async def test_numeracao_sequencial_por_guild(db: Database) -> None:
    assert (await abrir(db))[1] == 1
    assert (await abrir(db, opener_id=8))[1] == 2
    # Cada servidor tem sua própria sequência.
    assert (await abrir(db, guild_id=43))[1] == 1


async def test_canal_e_vinculado_depois_da_criacao(db: Database) -> None:
    """O número precisa ser reservado antes do canal, porque entra no nome."""
    ticket_id, numero = await abrir(db)
    assert (await db.get_ticket(ticket_id)).channel_id is None

    await db.set_ticket_channel(ticket_id, 555)
    assert (await db.ticket_by_channel(555)).numero == numero


async def test_falha_na_criacao_do_canal_descarta_o_ticket(db: Database) -> None:
    """Sem isso sobraria um ticket "aberto" travando a pessoa de abrir outro."""
    ticket_id, _ = await abrir(db)
    await db.drop_ticket(ticket_id)
    assert await db.get_ticket(ticket_id) is None
    assert await db.open_ticket_of(42, 7) is None


async def test_um_ticket_aberto_por_pessoa(db: Database) -> None:
    """A trava contra clique repetido encher o servidor de canais."""
    ticket_id, _ = await abrir(db)
    aberto = await db.open_ticket_of(42, 7)
    assert aberto is not None and aberto.id == ticket_id

    await db.close_ticket(ticket_id)
    assert await db.open_ticket_of(42, 7) is None


async def test_trava_nao_afeta_outras_pessoas_nem_guilds(db: Database) -> None:
    await abrir(db, opener_id=7)
    assert await db.open_ticket_of(42, 8) is None
    assert await db.open_ticket_of(43, 7) is None


async def test_fechar_duas_vezes_nao_conta(db: Database) -> None:
    ticket_id, _ = await abrir(db)
    assert await db.close_ticket(ticket_id) is True
    assert await db.close_ticket(ticket_id) is False


async def test_registro_sobrevive_ao_canal(db: Database) -> None:
    """Apagar o canal não pode apagar o histórico do atendimento."""
    ticket_id, numero = await abrir(db)
    await db.set_ticket_channel(ticket_id, 555)
    await db.close_ticket(ticket_id)
    await db.forget_ticket_channel(ticket_id)

    guardado = await db.get_ticket(ticket_id)
    assert guardado is not None
    assert guardado.channel_id is None
    assert guardado.numero == numero
    assert guardado.assunto == "preciso de ajuda"


async def test_contagem_de_abertos(db: Database) -> None:
    a, _ = await abrir(db, opener_id=7)
    await abrir(db, opener_id=8)
    await abrir(db, guild_id=43, opener_id=9)
    assert await db.count_open_tickets(42) == 2

    await db.close_ticket(a)
    assert await db.count_open_tickets(42) == 1


# ---------------------------------------------------------------------------
# Assumir
# ---------------------------------------------------------------------------


async def test_assumir_e_exclusivo(db: Database) -> None:
    """Dois cliques simultâneos não podem ambos assumir: o UPDATE só vale se
    ninguém tiver assumido antes."""
    ticket_id, _ = await abrir(db)
    assert await db.claim_ticket(ticket_id, 100) is True
    assert await db.claim_ticket(ticket_id, 200) is False
    assert (await db.get_ticket(ticket_id)).claimed_by == 100


# ---------------------------------------------------------------------------
# Botões persistentes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("acao", ["abrir", "assumir", "fechar", "apagar"])
def test_custom_id_casa_o_template(acao: str) -> None:
    """É o que faz painéis e tickets antigos voltarem a responder após um
    restart, sem nada carregado na inicialização."""
    template = TicketButton.__discord_ui_compiled_template__
    casou = template.fullmatch(f"ticket:{acao}:12")
    assert casou is not None
    assert casou["acao"] == acao
    assert casou["alvo"] == "12"


def test_template_ignora_acao_desconhecida() -> None:
    template = TicketButton.__discord_ui_compiled_template__
    assert template.fullmatch("ticket:explodir:1") is None
    assert template.fullmatch("srole:123") is None


def test_painel_tem_apenas_o_botao_de_abrir() -> None:
    view = painel_view()
    assert len(view.children) == 1
    assert view.children[0].custom_id == "ticket:abrir:0"


def ticket_falso(status: str = "aberto", claimed_by: int | None = None) -> Ticket:
    return Ticket(
        id=12,
        guild_id=42,
        numero=1,
        channel_id=555,
        opener_id=7,
        claimed_by=claimed_by,
        assunto="x",
        status=status,
        created_at=discord.utils.utcnow(),
    )


def test_ticket_aberto_mostra_assumir_e_fechar() -> None:
    ids = [c.custom_id for c in ticket_view(ticket_falso()).children]
    assert ids == ["ticket:assumir:12", "ticket:fechar:12"]


def test_assumir_desabilita_apos_alguem_assumir() -> None:
    # `disabled` mora no botão interno; o DynamicItem só proxia o custom_id.
    view = ticket_view(ticket_falso(claimed_by=100))
    assert view.children[0].item.disabled is True


def test_assumir_habilitado_enquanto_ninguem_assumiu() -> None:
    view = ticket_view(ticket_falso(claimed_by=None))
    assert view.children[0].item.disabled is False


def test_ticket_fechado_so_oferece_apagar() -> None:
    ids = [c.custom_id for c in ticket_view(ticket_falso(status="fechado")).children]
    assert ids == ["ticket:apagar:12"]


def test_nome_do_canal_e_ordenavel() -> None:
    """O zero à esquerda mantém a ordem alfabética igual à numérica na
    lista de canais."""
    assert nome_do_canal(1) == "ticket-0001"
    assert nome_do_canal(42) == "ticket-0042"
    assert sorted([nome_do_canal(2), nome_do_canal(10)]) == ["ticket-0002", "ticket-0010"]
