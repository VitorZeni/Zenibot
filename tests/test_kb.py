"""Leitura da base de conhecimento e publicação sem geração de texto."""

from __future__ import annotations

import pytest

from zenibot.cogs.kb import AVISOS, Conhecimento, corta
from zenibot.core import kb

CAPITULO = """---
fonte: "Kanon's Aion 2 Bible"
autor: "Kanon (twitch.tv/kanonxo)"
atualizado_em: 2026-08-23
capitulo: "CH 3: IDEAL STAT LINES"
idioma_conteudo: pt-BR
tags: [stat-lines, bis, accuracy, crit]
---

# 03 — Stat Lines ideais e caps

**Resumo:** a lista BIS slot a slot e os caps por raid.

**Pontos-chave:**
- A ordem é por raridade de roll, não por dano.
- Nunca use Synch Stone de grade inferior.

---

# CH 3: IDEAL STAT LINES

Toda peça vem com Stat Lines. O texto completo do capítulo mora aqui.
"""


@pytest.fixture
def acervo(tmp_path):
    """Diretório com dois capítulos e um índice que deve ser ignorado."""
    (tmp_path / "03-stat-lines.md").write_text(CAPITULO, encoding="utf-8")
    (tmp_path / "09-wings.md").write_text(
        CAPITULO.replace("CH 3", "CH 9")
        .replace("# 03 — Stat Lines ideais e caps", "# 09 — Wings")
        .replace("stat-lines, bis", "wings, asas"),
        encoding="utf-8",
    )
    (tmp_path / "INDICE.md").write_text("# Índice\nnão é capítulo", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_front_matter_vira_metadado() -> None:
    meta, resto = kb.separar_front_matter(CAPITULO)
    assert meta["fonte"] == "Kanon's Aion 2 Bible"
    assert meta["tags"] == ["stat-lines", "bis", "accuracy", "crit"]
    assert resto.lstrip().startswith("# 03")


def test_documento_sem_front_matter_nao_quebra() -> None:
    meta, resto = kb.separar_front_matter("# Só um título\ntexto")
    assert meta == {}
    assert resto.startswith("# Só um título")


def test_front_matter_invalido_e_tolerado() -> None:
    """Um YAML quebrado não pode derrubar o carregamento do acervo inteiro."""
    meta, _ = kb.separar_front_matter("---\n: : :\n  - [\n---\ncorpo")
    assert meta == {}


def test_resumo_para_no_primeiro_separador() -> None:
    """É o bloco antes do `---` que o bot publica — ele foi escrito para ter
    o tamanho de uma resposta."""
    _, corpo = kb.separar_front_matter(CAPITULO)
    resumo, capitulo = kb.separar_resumo(corpo)
    assert "Pontos-chave" in resumo
    assert "CH 3: IDEAL STAT LINES" in capitulo
    assert "Toda peça vem com" not in resumo


def test_sem_separador_o_documento_inteiro_vira_resumo() -> None:
    resumo, corpo = kb.separar_resumo("# Título\n\nSó isso.")
    assert "Só isso." in resumo
    assert corpo == ""


def test_documento_carregado(acervo) -> None:
    doc = kb.carregar_documento(acervo / "03-stat-lines.md")
    assert doc is not None
    assert doc.slug == "03-stat-lines"
    assert doc.titulo == "03 — Stat Lines ideais e caps"
    assert doc.autor.startswith("Kanon")
    assert "accuracy" in doc.tags


# ---------------------------------------------------------------------------
# Acervo
# ---------------------------------------------------------------------------


def test_indice_nao_entra_como_capitulo(acervo) -> None:
    slugs = [d.slug for d in kb.carregar_acervo(acervo)]
    assert "INDICE" not in slugs
    assert len(slugs) == 2


def test_ordem_alfabetica_e_a_ordem_editorial(acervo) -> None:
    """Os nomes têm prefixo numérico, então ordenar por nome basta."""
    assert [d.slug for d in kb.carregar_acervo(acervo)] == [
        "03-stat-lines",
        "09-wings",
    ]


def test_diretorio_ausente_devolve_vazio(tmp_path) -> None:
    """Acervo não configurado não pode impedir o bot de subir."""
    assert kb.carregar_acervo(None) == []
    assert kb.carregar_acervo(tmp_path / "nao-existe") == []


@pytest.mark.parametrize(
    ("termo", "esperado"),
    [
        ("wings", ["09-wings"]),
        ("asas", ["09-wings"]),           # busca por tag
        ("caps por raid", ["03-stat-lines", "09-wings"]),  # busca no resumo
        ("Synch Stone", ["03-stat-lines", "09-wings"]),
        ("", ["03-stat-lines", "09-wings"]),
        ("dragão", []),
    ],
)
def test_busca(acervo, termo, esperado) -> None:
    encontrados = [d.slug for d in kb.buscar(kb.carregar_acervo(acervo), termo)]
    assert encontrados == esperado


def test_busca_ignora_maiusculas(acervo) -> None:
    docs = kb.carregar_acervo(acervo)
    assert kb.buscar(docs, "WINGS") == kb.buscar(docs, "wings")


# ---------------------------------------------------------------------------
# Publicação
# ---------------------------------------------------------------------------


def test_publicacao_tira_titulo_e_nota_de_traducao() -> None:
    """O título vira o do embed, e a nota de tradução tem link relativo para
    outro arquivo — no Discord ele aparece quebrado."""
    resumo = (
        "# 03 — Stat Lines\n\n"
        "> **Tradução PT-BR.** Nomes mantidos em inglês.\n"
        "> Texto original: [`../base-conhecimento/03.md`](../base-conhecimento/03.md)\n\n"
        "**Resumo:** o conteúdo de verdade."
    )
    limpo = kb.resumo_publicavel(resumo)
    assert limpo.startswith("**Resumo:**")
    assert "base-conhecimento" not in limpo
    assert "# 03" not in limpo


def test_publicacao_preserva_citacao_no_meio_do_texto() -> None:
    """Citação no corpo é conteúdo; só a nota inicial é descarte."""
    resumo = "# T\n\n**Resumo:** algo.\n\n> Nota importante do autor."
    assert "> Nota importante do autor." in kb.resumo_publicavel(resumo)


def test_publicacao_de_resumo_ja_limpo_nao_muda_nada() -> None:
    assert kb.resumo_publicavel("**Resumo:** direto.") == "**Resumo:** direto."


def test_corta_preserva_linha_inteira() -> None:
    """Cortar no meio de uma tabela deixaria a mensagem ilegível."""
    texto = "\n".join(f"linha {i} com algum conteúdo" for i in range(200))
    cortado = corta(texto, 300)
    assert len(cortado) <= 300
    assert cortado.endswith("*(…continua no acervo)*")
    assert "linha 0" in cortado


def test_corta_nao_mexe_no_que_cabe() -> None:
    assert corta("curto", 100) == "curto"


def test_avisos_carregam_os_tres_pontos() -> None:
    """A fonte é work-in-progress e os números são de KR. Publicar sem estes
    avisos faria a comunidade tratar número de KR como verdade do Global."""
    assert "KR" in AVISOS
    assert "Front/Back Attack" in AVISOS
    assert "Ping" in AVISOS or "ping" in AVISOS


async def test_embed_publica_o_resumo_com_credito_e_avisos(bot, acervo) -> None:
    cog = Conhecimento.__new__(Conhecimento)
    cog.bot = bot
    cog.acervo = kb.carregar_acervo(acervo)

    embed = cog.montar(cog.por_slug("03-stat-lines"))

    # O bot recorta: o texto publicado é o do arquivo, não gerado.
    assert "Pontos-chave" in embed.description
    assert "Kanon" in embed.footer.text
    assert "2026-08-23" in embed.footer.text
    assert any("KR" in campo.value for campo in embed.fields)


async def test_slug_desconhecido_avisa(bot, acervo) -> None:
    from zenibot.core.checks import ZenibotError

    cog = Conhecimento.__new__(Conhecimento)
    cog.bot = bot
    cog.acervo = kb.carregar_acervo(acervo)

    with pytest.raises(ZenibotError, match="indice"):
        cog.por_slug("99-inexistente")


async def test_acervo_vazio_orienta_a_configurar(bot) -> None:
    from zenibot.core.checks import ZenibotError

    cog = Conhecimento.__new__(Conhecimento)
    cog.bot = bot
    cog.acervo = []

    with pytest.raises(ZenibotError, match="ZENIBOT_KB_PATH"):
        cog.exigir_acervo()
