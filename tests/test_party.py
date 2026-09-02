"""Grupos com vagas por função.

O teste central é a concorrência na última vaga: a checagem de lotação e a
inserção precisam acontecer na mesma instrução, senão dois cliques
simultâneos passam os dois.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from zenibot.cogs.party import PartyButton, montar_view
from zenibot.core import party
from zenibot.core.db import Database, now


async def criar(db: Database, **vagas) -> int:
    return await db.create_party(
        guild_id=42,
        owner_id=7,
        titulo="Raide de sexta",
        descricao="",
        inicio=None,
        vagas=vagas or {"tank": 1, "healer": 2, "dps": 3},
    )


async def vagas_de(db: Database, party_id: int) -> list[party.Vaga]:
    inscritos = await db.party_members(party_id)
    return [
        party.Vaga(f, cap, [u for u, ff in inscritos if ff == f])
        for f, cap in await db.party_slots(party_id)
    ]


# ---------------------------------------------------------------------------
# Entrar, sair, trocar
# ---------------------------------------------------------------------------


async def test_entrar_ocupa_a_vaga(db: Database) -> None:
    pid = await criar(db)
    assert await db.join_party(pid, 100, "tank") == "entrou"
    assert await db.party_role_of(pid, 100) == "tank"


async def test_clicar_na_propria_funcao_e_sair(db: Database) -> None:
    pid = await criar(db)
    await db.join_party(pid, 100, "tank")
    assert await db.join_party(pid, 100, "tank") == "saiu"
    assert await db.party_role_of(pid, 100) is None


async def test_clicar_em_outra_funcao_e_trocar(db: Database) -> None:
    pid = await criar(db)
    await db.join_party(pid, 100, "tank")
    assert await db.join_party(pid, 100, "dps") == "trocou"
    assert await db.party_role_of(pid, 100) == "dps"
    # A vaga de tank tem de voltar a ficar livre.
    assert await db.join_party(pid, 200, "tank") == "entrou"


async def test_uma_vaga_por_pessoa(db: Database) -> None:
    """A chave primária por (party, usuário) impede ocupar duas funções."""
    pid = await criar(db)
    await db.join_party(pid, 100, "tank")
    await db.join_party(pid, 100, "dps")
    inscritos = await db.party_members(pid)
    assert len([u for u, _ in inscritos if u == 100]) == 1


# ---------------------------------------------------------------------------
# Lotação
# ---------------------------------------------------------------------------


async def test_vaga_cheia_recusa(db: Database) -> None:
    pid = await criar(db, tank=1)
    assert await db.join_party(pid, 100, "tank") == "entrou"
    assert await db.join_party(pid, 200, "tank") == "cheia"
    assert await db.party_role_of(pid, 200) is None


async def test_troca_para_funcao_cheia_nao_custa_a_vaga_atual(db: Database) -> None:
    """Sem o rollback, tentar trocar para uma função lotada deixaria a pessoa
    sem função nenhuma — pior que simplesmente recusar."""
    pid = await criar(db, tank=1, dps=1)
    await db.join_party(pid, 100, "tank")
    await db.join_party(pid, 200, "dps")

    assert await db.join_party(pid, 100, "dps") == "cheia"
    assert await db.party_role_of(pid, 100) == "tank"


async def test_funcao_inexistente_e_recusada(db: Database) -> None:
    pid = await criar(db, tank=1)
    assert await db.join_party(pid, 100, "healer") == "cheia"


async def test_cliques_simultaneos_na_ultima_vaga(db: Database) -> None:
    """O ponto que motivou o desenho: a contagem e a inserção acontecem na
    mesma instrução SQL, então o banco decide o vencedor."""
    pid = await criar(db, tank=1)

    resultados = []
    for uid in (100, 200, 300, 400):
        resultados.append(await db.join_party(pid, uid, "tank"))

    assert resultados.count("entrou") == 1
    assert resultados.count("cheia") == 3
    inscritos = await db.party_members(pid)
    assert len(inscritos) == 1


async def test_concorrencia_real_nao_estoura_a_capacidade(db: Database) -> None:
    pid = await criar(db, dps=2)
    resultados = await asyncio.gather(
        *(db.join_party(pid, uid, "dps") for uid in range(100, 110))
    )
    assert resultados.count("entrou") == 2
    assert len(await db.party_members(pid)) == 2


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


async def test_encerrar_uma_vez_so(db: Database) -> None:
    pid = await criar(db)
    assert await db.close_party(pid) is True
    assert await db.close_party(pid) is False


async def test_drop_limpa_vagas_e_grupo(db: Database) -> None:
    """Se o painel não chega a ser publicado, não pode sobrar grupo órfão."""
    pid = await criar(db)
    await db.drop_party(pid)
    assert await db.get_party(pid) is None
    assert await db.party_slots(pid) == []


async def test_horario_e_evento_persistem(db: Database) -> None:
    inicio = now() + timedelta(hours=3)
    pid = await db.create_party(
        guild_id=42, owner_id=7, titulo="t", descricao="d",
        inicio=inicio, vagas={"livre": 5},
    )
    await db.set_party_event(pid, 999)
    grupo = await db.get_party(pid)
    assert grupo.event_id == 999
    assert abs((grupo.inicio - inicio).total_seconds()) < 1


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------


def vaga(funcao: str, cap: int, quantos: int = 0) -> party.Vaga:
    return party.Vaga(funcao, cap, list(range(quantos)))


def test_grupo_completo_quando_todas_cheias() -> None:
    assert party.completa([vaga("tank", 1, 1), vaga("dps", 2, 2)]) is True
    assert party.completa([vaga("tank", 1, 1), vaga("dps", 2, 1)]) is False


def test_grupo_sem_vagas_nao_conta_como_completo() -> None:
    assert party.completa([]) is False


def test_contagens() -> None:
    vagas = [vaga("tank", 1, 1), vaga("dps", 3, 2)]
    assert party.total_inscritos(vagas) == 3
    assert party.total_capacidade(vagas) == 4


def test_embed_mostra_progresso() -> None:
    embed = party.montar_embed(
        titulo="Raide", descricao="", dono_id=7,
        vagas=[vaga("tank", 1, 0), vaga("dps", 3, 2)],
    )
    assert "2/4" in embed.description
    assert len(embed.fields) == 2


def test_embed_anuncia_grupo_completo() -> None:
    embed = party.montar_embed(
        titulo="Raide", descricao="", dono_id=7, vagas=[vaga("tank", 1, 1)]
    )
    assert "completo" in embed.description


def test_embed_encerrado_tem_prioridade_sobre_completo() -> None:
    embed = party.montar_embed(
        titulo="Raide", descricao="", dono_id=7,
        vagas=[vaga("tank", 1, 1)], encerrada=True,
    )
    assert "encerrado" in embed.description.lower()


# ---------------------------------------------------------------------------
# Botões
# ---------------------------------------------------------------------------


def test_um_botao_por_funcao_mais_encerrar() -> None:
    vagas = [vaga("tank", 1), vaga("healer", 2), vaga("dps", 3)]
    ids = [c.custom_id for c in montar_view(12, vagas, encerrada=False).children]
    assert ids == [
        "party:entrar:12:tank",
        "party:entrar:12:healer",
        "party:entrar:12:dps",
        "party:encerrar:12:",
    ]


def test_grupo_encerrado_perde_os_botoes() -> None:
    vagas = [vaga("tank", 1)]
    assert montar_view(12, vagas, encerrada=True).children == []


def test_vaga_cheia_continua_clicavel() -> None:
    """Quem já está numa função lotada precisa poder sair dela."""
    view = montar_view(12, [vaga("tank", 1, 1)], encerrada=False)
    assert view.children[0].item.disabled is False


@pytest.mark.parametrize("acao", ["entrar", "encerrar"])
def test_custom_id_casa_o_template(acao: str) -> None:
    template = PartyButton.__discord_ui_compiled_template__
    casou = template.fullmatch(f"party:{acao}:12:tank")
    assert casou is not None
    assert casou["party"] == "12"


def test_template_ignora_outros_componentes() -> None:
    template = PartyButton.__discord_ui_compiled_template__
    assert template.fullmatch("ticket:abrir:0") is None
    assert template.fullmatch("party:explodir:1:") is None
