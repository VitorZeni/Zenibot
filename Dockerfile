# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Estágio 1: resolve as dependências num venv isolado.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Só o pyproject nesta camada: o cache do Docker só é invalidado quando as
# dependências mudam, não a cada alteração de código.
COPY pyproject.toml README.md ./
# Apenas os __init__.py: o setuptools exige que os diretórios declarados em
# [tool.setuptools] packages existam, mas estes arquivos são estáveis, então
# a camada de dependências continua sendo aproveitada do cache.
COPY zenibot/__init__.py ./zenibot/
COPY zenibot/cogs/__init__.py ./zenibot/cogs/
COPY zenibot/core/__init__.py ./zenibot/core/

# Instala o projeto para que o pip resolva as dependências declaradas no
# pyproject (fonte única da verdade) e em seguida remove o pacote em si:
# o código roda a partir de /app, não de site-packages. Isso importa porque
# config.py e db.py resolvem .env, o banco e migrations/ relativos à raiz do
# pacote — instalado no venv, essa raiz apontaria para o lugar errado.
RUN pip install . && pip uninstall -y zenibot

# ---------------------------------------------------------------------------
# Estágio 2: imagem final, sem ferramentas de build.
# ---------------------------------------------------------------------------
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Usuário sem privilégios: um bot com token não tem motivo para rodar como root.
RUN useradd --create-home --uid 10001 zenibot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=zenibot:zenibot migrations ./migrations
COPY --chown=zenibot:zenibot scripts ./scripts
COPY --chown=zenibot:zenibot zenibot ./zenibot

# O banco vive num volume; sem isto os dados morrem junto com o container.
RUN mkdir -p /app/data && chown zenibot:zenibot /app/data
VOLUME ["/app/data"]

USER zenibot

# Forma exec (sem shell): o python vira PID 1 e recebe o SIGTERM do
# `docker stop` diretamente. O handler em __main__.py encerra o bot de forma
# graciosa — fecha o WebSocket e o banco — em vez de ser morto no SIGKILL.
CMD ["python", "-m", "zenibot"]
