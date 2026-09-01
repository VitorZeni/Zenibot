"""Construtor de embeds: validação de entrada e estado do rascunho."""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from zenibot.cogs.builder import (
    LIMITE_AUTOR,
    LIMITE_DESCRICAO,
    LIMITE_RODAPE,
    LIMITE_TEXTINPUT,
    LIMITE_TITULO,
    LIMITE_TOTAL,
    BuilderView,
    embed_vazio,
    parse_cor,
    url_valida,
)

# ---------------------------------------------------------------------------
# Cor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entrada", ["#5865F2", "5865F2", "0x5865F2", "#5865f2"])
def test_parse_cor_aceita_variacoes_de_hex(entrada: str) -> None:
    """O `#` é opcional porque quase todo mundo cola o hex sem ele."""
    assert parse_cor(entrada) == discord.Colour(0x5865F2)


def test_parse_cor_vazia_devolve_none() -> None:
    assert parse_cor("") is None
    assert parse_cor("   ") is None


def test_parse_cor_aceita_atalho_de_tres_digitos() -> None:
    """`#58F` expande para `#5588FF`, como no CSS."""
    assert parse_cor("#123") == discord.Colour(0x112233)


@pytest.mark.parametrize(
    "entrada",
    [
        "zzz",
        "vermelho",
        "#GGGGGG",
        # O from_str do discord.py completaria com zero à esquerda e devolveria
        # #012345 sem reclamar — um erro de digitação viraria cor errada.
        "#12345",
        "#1234",
        "#12",
    ],
)
def test_parse_cor_rejeita_invalido(entrada: str) -> None:
    with pytest.raises(ValueError):
        parse_cor(entrada)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "ok"),
    [
        ("https://exemplo.com/a.png", True),
        ("http://exemplo.com/a.png", True),
        ("exemplo.com/a.png", False),
        ("javascript:alert(1)", False),
        ("data:image/png;base64,AAAA", False),
        ("", False),
    ],
)
def test_url_valida(valor: str, ok: bool) -> None:
    """Uma URL sem esquema http(s) faz o Discord devolver 400 ao publicar."""
    assert url_valida(valor) is ok


# ---------------------------------------------------------------------------
# Rascunho vazio
# ---------------------------------------------------------------------------


def test_embed_recem_criado_esta_vazio() -> None:
    assert embed_vazio(discord.Embed()) is True


def test_cor_sozinha_nao_torna_o_embed_publicavel() -> None:
    """O Discord recusa um embed sem conteúdo visível; cor não conta."""
    embed = discord.Embed(colour=discord.Colour.red())
    assert embed_vazio(embed) is True


@pytest.mark.parametrize(
    "preencher",
    [
        lambda e: setattr(e, "title", "t"),
        lambda e: setattr(e, "description", "d"),
        lambda e: e.set_footer(text="r"),
        lambda e: e.set_author(name="a"),
        lambda e: e.set_image(url="https://x/y.png"),
        lambda e: e.set_thumbnail(url="https://x/y.png"),
    ],
)
def test_qualquer_conteudo_visivel_torna_publicavel(preencher) -> None:
    embed = discord.Embed()
    preencher(embed)
    assert embed_vazio(embed) is False


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------


def test_limites_batem_com_os_do_discord() -> None:
    assert (LIMITE_TITULO, LIMITE_DESCRICAO) == (256, 4096)
    assert (LIMITE_RODAPE, LIMITE_AUTOR, LIMITE_TOTAL) == (2048, 256, 6000)


def test_textinput_nao_alcanca_a_descricao_inteira() -> None:
    """Um campo de modal aceita 4000 caracteres, menos que os 4096 da
    descrição — o painel não consegue preencher o máximo teórico."""
    assert LIMITE_TEXTINPUT == 4000
    assert LIMITE_TEXTINPUT < LIMITE_DESCRICAO


def test_contagem_de_caracteres_soma_todos_os_campos() -> None:
    """len(Embed) é o total que conta para o limite de 6000, não só o texto."""
    embed = discord.Embed(title="abc", description="de")
    embed.set_footer(text="fg")
    assert len(embed) == 7


# ---------------------------------------------------------------------------
# Estado do painel
# ---------------------------------------------------------------------------


def canal_falso(
    nome: str = "geral", canal_id: int = 10, *, publicavel: bool = True
) -> SimpleNamespace:
    perms = discord.Permissions(
        view_channel=publicavel, send_messages=publicavel, embed_links=publicavel
    )
    return SimpleNamespace(
        id=canal_id,
        mention=f"<#{canal_id}>",
        name=nome,
        guild=SimpleNamespace(me=object()),
        permissions_for=lambda _membro: perms,
    )


def test_previa_de_rascunho_vazio_mostra_orientacao() -> None:
    view = BuilderView(autor_id=1, canal=canal_falso())
    previa = view.preview()
    assert previa is not view.embed
    assert "Rascunho vazio" in (previa.title or "")


def test_previa_mostra_o_proprio_embed_quando_preenchido() -> None:
    view = BuilderView(autor_id=1, canal=canal_falso())
    view.embed.title = "Anúncio"
    assert view.preview() is view.embed


def test_status_mostra_o_canal_de_destino() -> None:
    view = BuilderView(autor_id=1, canal=canal_falso(canal_id=99))
    assert "<#99>" in view.status()


def test_status_avisa_quando_passa_do_limite() -> None:
    view = BuilderView(autor_id=1, canal=canal_falso())
    view.embed.description = "x" * (LIMITE_TOTAL + 1)
    assert "⚠️" in view.status()


def test_status_sem_aviso_dentro_do_limite() -> None:
    view = BuilderView(autor_id=1, canal=canal_falso())
    view.embed.title = "curto"
    assert "⚠️" not in view.status()


def test_status_avisa_de_canal_fechado_antes_de_publicar() -> None:
    """Descobrir que o canal é fechado só no Publicar, depois de montar o
    embed inteiro, é o pior momento possível."""
    view = BuilderView(autor_id=1, canal=canal_falso(publicavel=False))
    assert view.pode_publicar() is False
    assert "não posso publicar aqui" in view.status()


def test_status_normal_quando_o_canal_aceita() -> None:
    view = BuilderView(autor_id=1, canal=canal_falso(publicavel=True))
    assert view.pode_publicar() is True
    assert "não posso publicar" not in view.status()


def test_view_expira_para_nao_deixar_rascunho_eterno() -> None:
    """Ao contrário dos painéis de self-role, um rascunho não deve
    sobreviver indefinidamente — nem a um restart."""
    view = BuilderView(autor_id=1, canal=canal_falso())
    assert view.timeout is not None and view.timeout > 0
