"""Integração: eventos de Gateway com uma guild falsa.

Estes testes despacham eventos de verdade e verificam o que o bot publica —
o caminho completo que os testes unitários não alcançam.

## Escopo, e por que ele é estreito

O dpytest 0.7 não tem **nenhum** suporte a slash commands, interações ou
componentes: a API dele é da era dos comandos por prefixo. Como o Zenibot é
inteiramente slash e botões, só os *listeners* de evento são testáveis aqui:
`on_member_join` (boas-vindas, autorole, triagem, anti-raid).

Vale mesmo assim porque são justamente os caminhos que nenhum outro teste
alcança — eles não são chamados por comando, e sim pelo Gateway.

## Uma incompatibilidade contornada

`guild.create_role()` quebra: o discord.py 2.7 envia um campo `colors` que o
dpytest não conhece. Os testes usam a fábrica interna `backend.make_role`,
que produz o mesmo objeto sem passar pela camada HTTP simulada.
"""

from __future__ import annotations

import discord
import discord.ext.test as dpytest
import pytest
from discord.ext.test import backend as dpy_backend

from zenibot.bot import Zenibot


async def publicadas() -> list[discord.Message]:
    """Esvazia a fila de envios e devolve o que o bot publicou."""
    saida = []
    while not dpytest.sent_queue.empty():
        saida.append(await dpytest.sent_queue.get())
    return saida


def titulos(mensagens: list[discord.Message]) -> list[str]:
    return [m.embeds[0].title for m in mensagens if m.embeds]


def cria_cargo(guild: discord.Guild, nome: str) -> discord.Role:
    """Contorna o create_role, incompatível entre dpytest 0.7 e discord.py 2.7."""
    return dpy_backend.make_role(nome, guild)


# ---------------------------------------------------------------------------
# Boas-vindas
# ---------------------------------------------------------------------------


async def test_boas_vindas_publica_no_canal_configurado(dbot: Zenibot) -> None:
    guild = dbot.guilds[0]
    canal = guild.text_channels[0]
    await dbot.db.set_config(
        guild.id, welcome_channel_id=canal.id, welcome_enabled=True
    )

    await dpytest.member_join(0, name="novato")
    await dpytest.run_all_events()

    mensagens = await publicadas()
    assert "Novo membro" in titulos(mensagens)
    assert mensagens[0].channel.id == canal.id


async def test_boas_vindas_desligadas_nao_publicam(dbot: Zenibot) -> None:
    guild = dbot.guilds[0]
    await dbot.db.set_config(
        guild.id, welcome_channel_id=guild.text_channels[0].id, welcome_enabled=False
    )

    await dpytest.member_join(0, name="novato")
    await dpytest.run_all_events()

    assert await publicadas() == []


async def test_sem_canal_configurado_nao_quebra(dbot: Zenibot) -> None:
    """Guild recém-configurada não pode derrubar o listener."""
    await dpytest.member_join(0, name="novato")
    await dpytest.run_all_events()
    assert await publicadas() == []


async def test_uma_entrada_gera_uma_mensagem(dbot: Zenibot) -> None:
    """Duas mensagens por entrada seria spam; zero seria falha silenciosa."""
    guild = dbot.guilds[0]
    await dbot.db.set_config(
        guild.id, welcome_channel_id=guild.text_channels[0].id, welcome_enabled=True
    )

    await dpytest.member_join(0, name="novato")
    await dpytest.run_all_events()
    assert len(await publicadas()) == 1


# ---------------------------------------------------------------------------
# Triagem de conta nova
# ---------------------------------------------------------------------------


async def test_conta_nova_alerta_e_nao_recebe_autorole(dbot: Zenibot) -> None:
    guild = dbot.guilds[0]
    alertas = guild.text_channels[1]
    cargo = cria_cargo(guild, "Membro")

    await dbot.db.set_config(
        guild.id,
        alert_channel_id=alertas.id,
        autorole_id=cargo.id,
        min_account_age_days=365,  # qualquer conta criada agora cai nisto
    )

    membro = await dpytest.member_join(0, name="suspeito")
    await dpytest.run_all_events()

    assert "Conta recém-criada" in titulos(await publicadas())
    assert cargo not in membro.roles


# ---------------------------------------------------------------------------
# Anti-raid
# ---------------------------------------------------------------------------


async def test_pico_de_entradas_dispara_alerta(dbot: Zenibot) -> None:
    guild = dbot.guilds[0]
    canal = guild.text_channels[1]
    await dbot.db.set_config(
        guild.id, alert_channel_id=canal.id, raid_joins=3, raid_window_s=60
    )

    for i in range(3):
        await dpytest.member_join(0, name=f"raider{i}")
    await dpytest.run_all_events()

    assert "Possível raid detectado" in titulos(await publicadas())


async def test_alerta_nao_se_repete_no_mesmo_pico(dbot: Zenibot) -> None:
    """O silenciamento existe para um raid de 50 contas não gerar 48 alertas."""
    guild = dbot.guilds[0]
    canal = guild.text_channels[1]
    await dbot.db.set_config(
        guild.id, alert_channel_id=canal.id, raid_joins=3, raid_window_s=60
    )

    for i in range(8):
        await dpytest.member_join(0, name=f"raider{i}")
    await dpytest.run_all_events()

    alertas = [t for t in titulos(await publicadas()) if t == "Possível raid detectado"]
    assert len(alertas) == 1


async def test_entradas_abaixo_do_limiar_nao_alertam(dbot: Zenibot) -> None:
    guild = dbot.guilds[0]
    await dbot.db.set_config(
        guild.id,
        alert_channel_id=guild.text_channels[1].id,
        raid_joins=5,
        raid_window_s=60,
    )

    for i in range(4):
        await dpytest.member_join(0, name=f"membro{i}")
    await dpytest.run_all_events()

    assert "Possível raid detectado" not in titulos(await publicadas())


async def test_antiraid_desligado_ignora_o_pico(dbot: Zenibot) -> None:
    guild = dbot.guilds[0]
    await dbot.db.set_config(
        guild.id, alert_channel_id=guild.text_channels[1].id, raid_joins=0
    )

    for i in range(10):
        await dpytest.member_join(0, name=f"membro{i}")
    await dpytest.run_all_events()

    assert "Possível raid detectado" not in titulos(await publicadas())


# ---------------------------------------------------------------------------
# Interação entre os dois listeners
# ---------------------------------------------------------------------------


async def test_boas_vindas_e_antiraid_convivem(dbot: Zenibot) -> None:
    """Os dois escutam on_member_join; um não pode engolir o outro."""
    guild = dbot.guilds[0]
    await dbot.db.set_config(
        guild.id,
        welcome_channel_id=guild.text_channels[0].id,
        welcome_enabled=True,
        alert_channel_id=guild.text_channels[1].id,
        raid_joins=3,
        raid_window_s=60,
    )

    for i in range(3):
        await dpytest.member_join(0, name=f"pessoa{i}")
    await dpytest.run_all_events()

    achados = titulos(await publicadas())
    assert achados.count("Novo membro") == 3
    assert achados.count("Possível raid detectado") == 1


# ---------------------------------------------------------------------------
# Limite conhecido da ferramenta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recurso", ["slash commands", "botões", "modais", "selects"]
)
def test_dpytest_nao_cobre_interacoes(recurso: str) -> None:
    """Documenta o limite em vez de deixá-lo implícito.

    A API do dpytest 0.7 não expõe nada de interação. Se uma versão futura
    passar a expor, este teste falha e avisa que dá para ampliar a cobertura.
    """
    api = {n for n in dir(dpytest) if not n.startswith("_")}
    assert not {n for n in api if "interaction" in n.lower()}, (
        f"dpytest ganhou suporte a interações — dá para cobrir {recurso}"
    )
