"""Construtor de containers (Components V2).

O foco é o modelo de orçamento: o Discord limita a 40 componentes por
mensagem contando os aninhados, e os controles do painel consomem parte
disso enquanto se edita. Se a conta estiver errada, a publicação falha com
400 — então ela é verificada contra o payload real.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest
from discord import ui

from zenibot.cogs.containers import (
    CUSTO_BLOCO,
    CUSTO_CONTROLES,
    LIMITE_COMPONENTES,
    Bloco,
    ContainerBuilderView,
    cabe,
    custo_componentes,
    custo_texto,
    espaco_disponivel,
    montar_container,
    resumo,
)


def contar_componentes(payload: list[dict]) -> int:
    """Conta componentes como o Discord conta: aninhados incluídos."""
    total = 0
    for componente in payload:
        total += 1
        total += contar_componentes(componente.get("components") or [])
        if componente.get("accessory"):
            total += 1
        total += len(componente.get("items") or [])
    return total


def canal_falso(canal_id: int = 10, *, publicavel: bool = True) -> SimpleNamespace:
    perms = discord.Permissions(
        view_channel=publicavel, send_messages=publicavel, embed_links=publicavel
    )
    return SimpleNamespace(
        id=canal_id,
        mention=f"<#{canal_id}>",
        guild=SimpleNamespace(me=object()),
        permissions_for=lambda _m: perms,
    )


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------


def test_container_vazio_mostra_orientacao() -> None:
    container = montar_container([], None)
    assert any(isinstance(c, ui.TextDisplay) for c in container.children)


def test_montagem_respeita_a_ordem_dos_blocos() -> None:
    blocos = [
        Bloco("texto", texto="um"),
        Bloco("separador"),
        Bloco("imagem", url="https://x/y.png"),
    ]
    tipos = [type(c).__name__ for c in montar_container(blocos, None).children]
    assert tipos == ["TextDisplay", "Separator", "MediaGallery"]


def test_secao_leva_miniatura_como_acessorio() -> None:
    blocos = [Bloco("secao", texto="oi", url="https://x/y.png")]
    secao = montar_container(blocos, None).children[0]
    assert isinstance(secao, ui.Section)
    assert isinstance(secao.accessory, ui.Thumbnail)


def test_cor_vira_faixa_do_container() -> None:
    container = montar_container([Bloco("texto", texto="a")], discord.Colour(0x5865F2))
    assert container.accent_colour == discord.Colour(0x5865F2)


def test_markdown_e_preservado() -> None:
    """O texto vai cru para o TextDisplay: é o que permite títulos e listas."""
    container = montar_container([Bloco("texto", texto="# Título\n- item")], None)
    assert container.children[0].content == "# Título\n- item"


# ---------------------------------------------------------------------------
# Orçamento de componentes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", list(CUSTO_BLOCO))
def test_custo_por_bloco_bate_com_o_payload_real(tipo: str) -> None:
    """Se o discord.py mudar como serializa um bloco, a conta quebra aqui —
    e não em produção, com um 400 no Publicar."""
    bloco = Bloco(tipo, texto="x", url="https://x/y.png")
    container = montar_container([bloco], None)
    real = contar_componentes([container.to_component_dict()])
    assert real == 1 + CUSTO_BLOCO[tipo]


def test_custo_total_soma_moldura_mais_blocos() -> None:
    blocos = [Bloco("texto", texto="a"), Bloco("separador")]
    assert custo_componentes(blocos) == 1 + 1 + 1


def test_custo_texto_ignora_blocos_sem_texto() -> None:
    blocos = [
        Bloco("texto", texto="abc"),
        Bloco("separador"),
        Bloco("imagem", url="https://x/y.png"),
        Bloco("secao", texto="de", url="https://x/y.png"),
    ]
    assert custo_texto(blocos) == 5


def test_espaco_desconta_os_controles_do_painel() -> None:
    assert espaco_disponivel([]) == LIMITE_COMPONENTES - CUSTO_CONTROLES - 1


def test_cabe_fecha_quando_o_orcamento_acaba() -> None:
    blocos = [Bloco("texto", texto="x") for _ in range(espaco_disponivel([]))]
    assert espaco_disponivel(blocos) == 0
    assert cabe(blocos, "texto") is False
    assert cabe(blocos, "secao") is False


# ---------------------------------------------------------------------------
# O painel montado
# ---------------------------------------------------------------------------


def test_custo_dos_controles_bate_com_o_painel_real() -> None:
    """CUSTO_CONTROLES é uma constante escrita à mão; este teste a confronta
    com o payload que o painel realmente gera."""
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso())
    painel.blocos = [Bloco("texto", texto="a")]
    painel.render()

    total = contar_componentes(painel.to_components())
    assert total == custo_componentes(painel.blocos) + CUSTO_CONTROLES


def test_painel_cheio_nao_estoura_o_limite_do_discord() -> None:
    """Propriedade central: enquanto `cabe` autorizar, o painel renderizado
    tem de caber nos 40 componentes."""
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso())
    while cabe(painel.blocos, "texto"):
        painel.blocos.append(Bloco("texto", texto="x"))
    painel.render()

    assert contar_componentes(painel.to_components()) <= LIMITE_COMPONENTES


def test_botoes_desabilitam_quando_o_orcamento_acaba() -> None:
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso())
    assert painel.acoes.add_texto.disabled is False

    while cabe(painel.blocos, "texto"):
        painel.blocos.append(Bloco("texto", texto="x"))
    painel.render()

    assert painel.acoes.add_texto.disabled is True


def test_acoes_de_edicao_desabilitadas_sem_blocos() -> None:
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso())
    assert painel.editar.publicar.disabled is True
    assert painel.editar.desfazer.disabled is True

    painel.blocos.append(Bloco("texto", texto="a"))
    painel.render()
    assert painel.editar.publicar.disabled is False


def test_status_avisa_de_canal_fechado() -> None:
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso(publicavel=False))
    assert painel.pode_publicar() is False
    assert "não posso publicar aqui" in painel.status()


def test_status_lista_os_blocos_na_ordem() -> None:
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso())
    painel.blocos = [Bloco("texto", texto="a"), Bloco("imagem", url="https://x/y.png")]
    assert resumo(painel.blocos) == "1. Texto · 2. Imagem"


def test_painel_expira_para_nao_deixar_rascunho_eterno() -> None:
    painel = ContainerBuilderView(autor_id=1, canal=canal_falso())
    assert painel.timeout is not None and painel.timeout > 0


def test_painel_trata_erro_de_componente() -> None:
    """Sem on_error, o padrão do discord.py só loga e o usuário vê
    "não respondeu a tempo"."""
    assert "on_error" in vars(ContainerBuilderView)


def test_publicado_nao_leva_os_controles() -> None:
    """O que vai para o canal é só o container — sem status nem botões."""
    blocos = [Bloco("texto", texto="anúncio")]
    publicado = ui.LayoutView(timeout=None)
    publicado.add_item(montar_container(blocos, None))
    payload = publicado.to_components()
    assert len(payload) == 1
    assert contar_componentes(payload) == custo_componentes(blocos)
