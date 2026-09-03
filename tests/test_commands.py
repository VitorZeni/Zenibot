"""Corpo dos slash commands, com banco real e Discord falso.

Os decoradores (`is_staff`, `default_permissions`) já são testados à parte,
então aqui o corpo é invocado direto via `Command.callback`. O que se
verifica é o efeito: o que foi gravado, o que foi enviado, e o que o
moderador viu de volta.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeChannel, FakeGuild, FakeInteraction, FakeMember, FakeRole
from zenibot.bot import Zenibot
from zenibot.cogs.admin import Admin
from zenibot.cogs.moderation import Moderation
from zenibot.cogs.party import Party
from zenibot.cogs.roles import Roles
from zenibot.cogs.tickets import Tickets
from zenibot.core.checks import ZenibotError


def cenario():
    """Guild com moderador alto, alvo baixo e canal de log.

    `owner_id` é de um terceiro de propósito: o dono do servidor ignora a
    checagem de hierarquia, e um moderador que fosse dono mascararia o teste.
    """
    canal_log = FakeChannel(70, "logs")
    guild = FakeGuild(canais=[FakeChannel(10, "geral"), canal_log], owner_id=555)
    moderador = guild.adiciona_membro(
        FakeMember(1, "mod", roles=[FakeRole(9, "Staff", 50)])
    )
    alvo = guild.adiciona_membro(
        FakeMember(2, "infrator", roles=[FakeRole(8, "Membro", 5)])
    )
    return guild, moderador, alvo, canal_log


async def executa(bot: Zenibot, comando, interaction, **kwargs):
    """Invoca o corpo do comando com o cog real do bot.

    Um `Command` acessado pela classe vem sem `binding`, então o cog é
    resolvido pelo qualname do callback ("Moderation.warn" -> "Moderation").
    """
    nome_cog = comando.callback.__qualname__.split(".")[0]
    cog = bot.get_cog(nome_cog)
    assert cog is not None, f"cog {nome_cog} não carregado"
    return await comando.callback(cog, interaction, **kwargs)


# ---------------------------------------------------------------------------
# /aviso
# ---------------------------------------------------------------------------


async def test_aviso_grava_caso_avisa_por_dm_e_espelha_no_log(bot: Zenibot) -> None:
    guild, mod, alvo, canal_log = cenario()
    await bot.db.set_config(guild.id, log_channel_id=canal_log.id)
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(
        bot, Moderation.warn, interaction, membro=alvo, motivo="spam no chat"
    )

    caso = await bot.db.get_case(guild.id, 1)
    assert caso is not None and caso.reason == "spam no chat"
    assert caso.user_id == alvo.id and caso.moderator_id == mod.id

    assert len(alvo.dms) == 1, "o alvo precisa ser avisado"
    assert len(canal_log.enviadas) == 1, "o caso precisa aparecer no log"
    assert "#1" in interaction.followup.enviado["embed"].description


async def test_aviso_com_dm_fechada_ainda_registra(bot: Zenibot) -> None:
    """DM fechada é comum e não pode impedir a moderação."""
    guild, mod, _, _ = cenario()
    alvo = FakeMember(2, "fechado", roles=[FakeRole(8, "M", 5)], dm_fechada=True)
    alvo.guild = guild
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(bot, Moderation.warn, interaction, membro=alvo, motivo="x")

    assert await bot.db.get_case(guild.id, 1) is not None


async def test_aviso_recusa_alvo_de_cargo_igual_ou_maior(bot: Zenibot) -> None:
    guild, mod, _, _ = cenario()
    poderoso = FakeMember(3, "chefe", roles=[FakeRole(7, "Chefe", 90)])
    poderoso.guild = guild
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    with pytest.raises(ZenibotError, match="cargo igual ou superior"):
        await executa(bot, Moderation.warn, interaction, membro=poderoso, motivo="x")

    assert await bot.db.get_case(guild.id, 1) is None


async def test_aviso_recusa_auto_moderacao(bot: Zenibot) -> None:
    guild, mod, _, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    with pytest.raises(ZenibotError, match="a si mesmo"):
        await executa(bot, Moderation.warn, interaction, membro=mod, motivo="x")


async def test_aviso_no_limiar_dispara_escalonamento(bot: Zenibot) -> None:
    """A régua só age quando a contagem bate o limiar exato."""
    guild, mod, alvo, _ = cenario()
    await bot.db.set_escalation_rule(guild.id, 2, "timeout", 3600)
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(bot, Moderation.warn, interaction, membro=alvo, motivo="1a")
    assert alvo.silenciado is None, "não deve punir na primeira"

    i2 = FakeInteraction(client=bot, guild=guild, user=mod)
    await executa(bot, Moderation.warn, i2, membro=alvo, motivo="2a")

    assert alvo.silenciado is not None, "a 2a infração devia punir"
    assert "Escalonamento" in i2.followup.enviado["embed"].description


async def test_punicao_automatica_nao_realimenta_a_regua(bot: Zenibot) -> None:
    """O caso gerado pelo escalonamento não pode contar como nova infração."""
    guild, mod, alvo, _ = cenario()
    await bot.db.set_escalation_rule(guild.id, 2, "timeout", 3600)

    for motivo in ("1a", "2a"):
        i = FakeInteraction(client=bot, guild=guild, user=mod)
        await executa(bot, Moderation.warn, i, membro=alvo, motivo=motivo)

    # Dois avisos + uma punição automática no histórico, mas a contagem
    # que a régua enxerga continua sendo 2.
    assert len(await bot.db.get_user_cases(guild.id, alvo.id)) == 3
    assert await bot.db.count_active_cases(guild.id, alvo.id) == 2


# ---------------------------------------------------------------------------
# /silenciar
# ---------------------------------------------------------------------------


async def test_silenciar_aplica_timeout(bot: Zenibot) -> None:
    guild, mod, alvo, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(
        bot, Moderation.timeout, interaction, membro=alvo, duracao="2h", motivo="flood"
    )

    assert alvo.silenciado is not None
    caso = await bot.db.get_case(guild.id, 1)
    assert caso.action == "timeout" and caso.duration_s == 7200


async def test_silenciar_acima_de_28_dias_e_recusado(bot: Zenibot) -> None:
    """Limite do Discord: acima disso a API recusaria."""
    guild, mod, alvo, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    with pytest.raises(ZenibotError, match="28 dias"):
        await executa(
            bot, Moderation.timeout, interaction, membro=alvo, duracao="40d", motivo="x"
        )

    assert alvo.silenciado is None


async def test_silenciar_com_duracao_invalida(bot: Zenibot) -> None:
    guild, mod, alvo, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    with pytest.raises(ZenibotError, match="inválida"):
        await executa(
            bot, Moderation.timeout, interaction, membro=alvo, duracao="ontem", motivo="x"
        )


# ---------------------------------------------------------------------------
# /banir
# ---------------------------------------------------------------------------


async def test_banir_temporario_agenda_o_desbanimento(bot: Zenibot) -> None:
    guild, mod, alvo, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(
        bot, Moderation.ban, interaction, usuario=alvo, motivo="grave", duracao="7d"
    )

    assert guild.banidos, "o ban precisa chegar à API"
    job = await bot.db.get_pending_job(guild.id, "unban")
    assert job is not None and job.user_id == alvo.id


async def test_banir_permanente_nao_agenda_nada(bot: Zenibot) -> None:
    guild, mod, alvo, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(bot, Moderation.ban, interaction, usuario=alvo, motivo="grave")

    assert guild.banidos
    assert await bot.db.get_pending_job(guild.id, "unban") is None


async def test_desbanir_cancela_o_agendamento(bot: Zenibot) -> None:
    """Desbanir na mão não pode deixar um job órfão para depois."""
    guild, mod, alvo, _ = cenario()
    i1 = FakeInteraction(client=bot, guild=guild, user=mod)
    await executa(bot, Moderation.ban, i1, usuario=alvo, motivo="x", duracao="7d")

    i2 = FakeInteraction(client=bot, guild=guild, user=mod)
    await executa(bot, Moderation.unban, i2, usuario=alvo, motivo="revisto")

    assert guild.desbanidos == [alvo.id]
    assert await bot.db.get_pending_job(guild.id, "unban") is None


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------


async def test_caso_inexistente_avisa(bot: Zenibot) -> None:
    guild, mod, _, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    with pytest.raises(ZenibotError, match="não existe"):
        await executa(bot, Moderation.case, interaction, numero=42)


async def test_historico_vazio_diz_que_esta_limpo(bot: Zenibot) -> None:
    guild, mod, alvo, _ = cenario()
    interaction = FakeInteraction(client=bot, guild=guild, user=mod)

    await executa(bot, Moderation.history, interaction, usuario=alvo)

    assert "não tem registros" in interaction.texto


# ---------------------------------------------------------------------------
# Guardas de configuração
# ---------------------------------------------------------------------------


async def test_config_canal_recusa_canal_sem_permissao(bot: Zenibot) -> None:
    """Checar agora evita descobrir o problema na primeira mensagem perdida."""
    from discord import app_commands

    fechado = FakeChannel(80, "fechado", view_channel=True, send_messages=False)
    guild = FakeGuild(canais=[fechado])
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(1))

    with pytest.raises(ZenibotError, match="Enviar Mensagens"):
        await executa(
            bot,
            Admin.config_channel,
            interaction,
            tipo=app_commands.Choice(name="Logs", value="log_channel_id"),
            canal=fechado,
        )

    assert (await bot.db.get_config(guild.id)).log_channel_id is None


async def test_ticket_painel_exige_categoria_configurada(bot: Zenibot) -> None:
    guild = FakeGuild(canais=[FakeChannel(10, "suporte")])
    interaction = FakeInteraction(
        client=bot, guild=guild, user=FakeMember(1), channel=guild.get_channel(10)
    )

    with pytest.raises(ZenibotError, match="categoria"):
        await executa(bot, Tickets.painel, interaction, canal=None)


# ---------------------------------------------------------------------------
# /grupo criar
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Janela de 3 segundos da interação
# ---------------------------------------------------------------------------


async def test_cargo_criar_avisa_antes_de_chamar_a_api(bot: Zenibot) -> None:
    """Regressão de um erro visto em produção: com o gateway lento, um comando
    que chama a API antes de responder perde a janela de 3 segundos e o
    usuário vê "o aplicativo não respondeu" — com o efeito já aplicado.

    Responder pelo followup só é possível depois de um defer, então é isso que
    se verifica aqui.
    """
    guild = FakeGuild()
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(1))

    await executa(bot, Roles.criar, interaction, nome="Raider", cor="vermelho")

    assert interaction.response.enviado is None, "não devia responder direto"
    assert interaction.followup.enviado is not None, "faltou o defer + followup"
    assert any(c.name == "Raider" for c in guild.roles)


async def test_cargo_editar_avisa_antes_de_chamar_a_api(bot: Zenibot) -> None:
    guild = FakeGuild()
    alvo = FakeRole(500, "Antigo", 10)
    interaction = FakeInteraction(client=bot, guild=guild, user=FakeMember(1))

    await executa(bot, Roles.editar, interaction, cargo=alvo, nome="Novo")

    assert interaction.response.enviado is None
    assert interaction.followup.enviado is not None


async def test_caso_avisa_antes_dos_dois_fetch(bot: Zenibot, monkeypatch) -> None:
    """`/caso` fazia dois round-trips ao Discord antes de dar sinal de vida."""
    guild, mod, alvo, _ = cenario()
    i1 = FakeInteraction(client=bot, guild=guild, user=mod)
    await executa(bot, Moderation.warn, i1, membro=alvo, motivo="x")

    # fetch_user vai à API; aqui interessa só a ordem entre responder e buscar.
    async def fetch_falso(user_id: int) -> FakeMember:
        return FakeMember(user_id, f"user{user_id}")

    monkeypatch.setattr(bot, "fetch_user", fetch_falso)

    i2 = FakeInteraction(client=bot, guild=guild, user=mod)
    await executa(bot, Moderation.case, i2, numero=1)

    assert i2.response.enviado is None
    assert i2.followup.enviado is not None


async def test_grupo_sem_vagas_usa_o_padrao(bot: Zenibot) -> None:
    """Sem nenhuma vaga o painel não teria botão nenhum."""
    guild = FakeGuild(canais=[FakeChannel(10, "geral")])
    interaction = FakeInteraction(
        client=bot, guild=guild, user=FakeMember(1), channel=guild.get_channel(10)
    )

    await executa(bot, Party.criar, interaction, titulo="Jogatina")

    grupo = await bot.db.get_party(1)
    assert grupo is not None
    assert await bot.db.party_slots(1) == [("livre", 5)]


async def test_grupo_com_evento_exige_horario(bot: Zenibot) -> None:
    guild = FakeGuild(canais=[FakeChannel(10, "geral")])
    interaction = FakeInteraction(
        client=bot, guild=guild, user=FakeMember(1), channel=guild.get_channel(10)
    )

    with pytest.raises(ZenibotError, match="quando"):
        await executa(bot, Party.criar, interaction, titulo="Raide", evento=True)

    assert await bot.db.get_party(1) is None
