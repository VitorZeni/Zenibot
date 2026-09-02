"""Modelos salvos: persistência e ida-e-volta da serialização.

O teste central é o round-trip: um modelo salvo precisa voltar idêntico, ou
"reutilizar" entrega algo diferente do que foi montado.
"""

from __future__ import annotations

import discord
import pytest

from zenibot.cogs.builder import embed_para_payload, payload_para_embed
from zenibot.cogs.containers import (
    Bloco,
    blocos_para_payload,
    montar_container,
    payload_para_blocos,
)
from zenibot.core.db import Database

# ---------------------------------------------------------------------------
# Serialização de embed
# ---------------------------------------------------------------------------


def test_embed_volta_identico() -> None:
    original = discord.Embed(
        title="Regras", description="Leia tudo", colour=discord.Colour(0x5865F2)
    )
    original.set_footer(text="rodapé")
    original.set_author(name="Zenibot")
    original.set_image(url="https://x/y.png")
    original.set_thumbnail(url="https://x/z.png")

    volta = payload_para_embed(embed_para_payload(original))
    assert volta.to_dict() == original.to_dict()


def test_embed_vazio_sobrevive_ao_round_trip() -> None:
    original = discord.Embed()
    assert payload_para_embed(embed_para_payload(original)).to_dict() == original.to_dict()


# ---------------------------------------------------------------------------
# Serialização de container
# ---------------------------------------------------------------------------


def test_container_volta_identico() -> None:
    blocos = [
        Bloco("texto", texto="# Título"),
        Bloco("separador"),
        Bloco("secao", texto="lado a lado", url="https://x/y.png"),
        Bloco("imagem", url="https://x/z.png"),
    ]
    cor = discord.Colour(0x2ECC71)

    volta, cor_volta = payload_para_blocos(blocos_para_payload(blocos, cor))
    assert volta == blocos
    assert cor_volta == cor


def test_container_sem_cor() -> None:
    blocos, cor = payload_para_blocos(blocos_para_payload([Bloco("separador")], None))
    assert cor is None
    assert blocos == [Bloco("separador")]


def test_payload_corrompido_ignora_blocos_desconhecidos() -> None:
    """Um modelo salvo por uma versão futura não pode derrubar o editor."""
    payload = {
        "cor": None,
        "blocos": [
            {"tipo": "texto", "texto": "ok"},
            {"tipo": "widget_do_futuro", "texto": "?"},
        ],
    }
    blocos, _ = payload_para_blocos(payload)
    assert [b.tipo for b in blocos] == ["texto"]


def test_modelo_reconstroi_o_mesmo_container() -> None:
    blocos = [Bloco("texto", texto="oi"), Bloco("imagem", url="https://x/y.png")]
    payload = blocos_para_payload(blocos, discord.Colour(0x123456))

    de_volta, cor = payload_para_blocos(payload)
    a = montar_container(blocos, discord.Colour(0x123456)).to_component_dict()
    b = montar_container(de_volta, cor).to_component_dict()
    assert a == b


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------


async def salvar(db: Database, nome: str = "regras", tipo: str = "embed", **kw) -> bool:
    return await db.save_template(
        guild_id=kw.get("guild_id", 42),
        nome=nome,
        tipo=tipo,
        payload=kw.get("payload", {"embed": {"title": "t"}}),
        autor_id=7,
    )


async def test_salvar_e_recuperar(db: Database) -> None:
    assert await salvar(db) is True
    modelo = await db.get_template(42, "regras")
    assert modelo is not None
    assert modelo.tipo == "embed"
    assert modelo.payload["embed"]["title"] == "t"
    assert modelo.criado_por == 7


async def test_regravar_atualiza_em_vez_de_duplicar(db: Database) -> None:
    await salvar(db)
    novo = await salvar(db, payload={"embed": {"title": "outro"}})
    assert novo is False, "regravar deveria atualizar, não criar"
    assert await db.count_templates(42) == 1
    assert (await db.get_template(42, "regras")).payload["embed"]["title"] == "outro"


async def test_modelos_isolam_guilds(db: Database) -> None:
    await salvar(db, guild_id=42)
    assert await db.get_template(43, "regras") is None
    assert await db.list_templates(43) == []


async def test_listagem_ordenada_por_nome(db: Database) -> None:
    for nome in ("zulu", "alfa", "meio"):
        await salvar(db, nome=nome)
    assert [m.nome for m in await db.list_templates(42)] == ["alfa", "meio", "zulu"]


async def test_apagar(db: Database) -> None:
    await salvar(db)
    assert await db.delete_template(42, "regras") is True
    assert await db.delete_template(42, "regras") is False
    assert await db.get_template(42, "regras") is None


async def test_contagem_por_guild(db: Database) -> None:
    await salvar(db, nome="a")
    await salvar(db, nome="b")
    await salvar(db, nome="c", guild_id=43)
    assert await db.count_templates(42) == 2
    assert await db.count_templates(43) == 1


@pytest.mark.parametrize("tipo", ["embed", "container"])
async def test_guarda_os_dois_tipos(db: Database, tipo: str) -> None:
    await salvar(db, nome=f"m-{tipo}", tipo=tipo)
    assert (await db.get_template(42, f"m-{tipo}")).tipo == tipo
