"""Leitura de uma base de conhecimento em Markdown com front matter.

O acervo **não** vive no repositório: ele é obra de terceiros e o repositório
é público. O bot lê de um diretório apontado por `ZENIBOT_KB_PATH`, montado
como volume no Docker. Aqui mora só o leitor.

## Formato esperado

Cada arquivo `.md` tem front matter YAML e, logo depois, um bloco de resumo
em português separado do capítulo por uma linha `---`:

    ---
    fonte: "..."
    autor: "..."
    capitulo: "CH 3: IDEAL STAT LINES"
    tags: [stat-lines, bis]
    ---

    # 03 — Título

    **Resumo:** ...
    **Pontos-chave:** ...

    ---

    (capítulo completo)

É o bloco antes do `---` que o bot publica: ele foi escrito para ter o
tamanho de uma resposta. O capítulo inteiro fica disponível em `corpo`, para
quem quiser paginar depois.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Linha que só tem três hifens — o separador entre resumo e capítulo.
SEPARADOR = re.compile(r"^---\s*$", re.MULTILINE)

# Arquivos que descrevem o acervo, e não são capítulos.
IGNORAR = {"indice.md", "readme.md", "index.md"}


@dataclass(slots=True)
class Documento:
    slug: str
    titulo: str
    capitulo: str
    resumo: str
    corpo: str
    tags: list[str] = field(default_factory=list)
    fonte: str = ""
    autor: str = ""
    atualizado_em: str = ""

    @property
    def rotulo(self) -> str:
        """Como o documento aparece no autocomplete."""
        return f"{self.slug} · {self.titulo}"[:100]

    def casa(self, termo: str) -> bool:
        termo = termo.lower().strip()
        if not termo:
            return True
        alvo = " ".join(
            [self.slug, self.titulo, self.capitulo, " ".join(self.tags), self.resumo]
        ).lower()
        return termo in alvo


def separar_front_matter(texto: str) -> tuple[dict, str]:
    """Devolve (metadados, resto). Sem front matter, devolve ({}, texto)."""
    if not texto.startswith("---"):
        return {}, texto

    fim = SEPARADOR.search(texto, 3)
    if fim is None:
        return {}, texto

    bruto = texto[3 : fim.start()]
    try:
        meta = yaml.safe_load(bruto) or {}
    except yaml.YAMLError:
        log.warning("Front matter inválido — documento tratado como sem metadados")
        return {}, texto
    if not isinstance(meta, dict):
        return {}, texto
    return meta, texto[fim.end() :]


def separar_resumo(corpo: str) -> tuple[str, str]:
    """Separa o bloco de resumo do capítulo, no primeiro `---` solitário.

    Sem separador, o documento inteiro vira resumo — melhor publicar algo do
    que nada, e o corte por tamanho acontece na hora de montar o embed.
    """
    corte = SEPARADOR.search(corpo)
    if corte is None:
        return corpo.strip(), ""
    return corpo[: corte.start()].strip(), corpo[corte.end() :].strip()


def resumo_publicavel(resumo: str) -> str:
    """Limpa o resumo do que só faz sentido dentro do repositório.

    Duas coisas saem: o título `# ...`, que vira o título do embed e ficaria
    duplicado, e o bloco de citação inicial com a nota de tradução — ela tem
    link relativo para outro arquivo, que no Discord aparece quebrado.

    Citações no meio do texto são conteúdo e permanecem.
    """
    linhas = resumo.splitlines()

    while linhas and not linhas[0].strip():
        linhas.pop(0)
    if linhas and linhas[0].startswith("# "):
        linhas.pop(0)
    while linhas and not linhas[0].strip():
        linhas.pop(0)
    while linhas and linhas[0].lstrip().startswith(">"):
        linhas.pop(0)

    return "\n".join(linhas).strip()


def primeiro_titulo(texto: str) -> str:
    for linha in texto.splitlines():
        if linha.startswith("# "):
            return linha[2:].strip()
    return ""


def carregar_documento(caminho: Path) -> Documento | None:
    try:
        texto = caminho.read_text(encoding="utf-8")
    except OSError:
        log.warning("Não consegui ler %s", caminho)
        return None

    meta, corpo = separar_front_matter(texto)
    resumo, capitulo_texto = separar_resumo(corpo)
    titulo = primeiro_titulo(resumo) or caminho.stem

    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    return Documento(
        slug=caminho.stem,
        titulo=titulo,
        capitulo=str(meta.get("capitulo", "")),
        resumo=resumo,
        corpo=capitulo_texto,
        tags=[str(t) for t in tags],
        fonte=str(meta.get("fonte", "")),
        autor=str(meta.get("autor", "")),
        atualizado_em=str(meta.get("atualizado_em", "")),
    )


def carregar_acervo(diretorio: Path | None) -> list[Documento]:
    """Todos os capítulos do diretório, em ordem de nome.

    Os nomes têm prefixo numérico, então a ordem alfabética é a ordem
    editorial. Diretório inexistente devolve lista vazia — o cog carrega
    igual e os comandos avisam que o acervo não está configurado.
    """
    if diretorio is None or not diretorio.is_dir():
        return []

    documentos = []
    for caminho in sorted(diretorio.glob("*.md")):
        if caminho.name.lower() in IGNORAR:
            continue
        doc = carregar_documento(caminho)
        if doc is not None:
            documentos.append(doc)
    return documentos


def buscar(acervo: list[Documento], termo: str, limite: int = 25) -> list[Documento]:
    return [doc for doc in acervo if doc.casa(termo)][:limite]
