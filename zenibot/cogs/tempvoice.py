"""Canais de voz temporários: entrar no saguão cria o seu.

Resolve o problema de servidores com dezenas de canais de voz vazios: em vez
de manter salas fixas para todo caso possível, existe um único canal-saguão.
Quem entra nele ganha um canal próprio e é movido para lá; o canal some
quando esvazia.

Três decisões de desenho, todas sobre não deixar lixo para trás:

1. **A exclusão só é disparada por evento de saída** de um canal temporário.
   Isso garante que ele teve alguém dentro, o que elimina a corrida entre
   criar o canal e mover a pessoa — janela em que ele está legitimamente
   vazio.

2. **Varredura na inicialização.** Se o bot cair com canais abertos, ninguém
   os apaga. A tabela existe justamente para saber quais são nossos depois de
   um restart.

3. **Quem já tem um canal é movido para ele**, em vez de ganhar outro. Sem
   isso, entrar e sair do saguão em sequência viraria uma fila de canais
   órfãos com o mesmo dono.

O dono recebe permissões no próprio canal, então renomeia e ajusta vagas pela
interface do Discord — sem painel do bot no meio.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.guild import MAX_POR_CATEGORIA, categoria_cheia

log = logging.getLogger(__name__)



def nome_do_canal(membro: discord.Member) -> str:
    return f"🔊 {membro.display_name}"[:100]


class TempVoice(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot
        self._varreu = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Varre órfãos uma vez por processo.

        `on_ready` dispara de novo a cada reconexão; varrer toda vez seria
        desperdício de chamadas à API.
        """
        if self._varreu:
            return
        self._varreu = True
        await self.limpar_orfaos()

    async def limpar_orfaos(self) -> int:
        """Apaga canais temporários que sobraram de uma execução anterior."""
        removidos = 0
        for channel_id, guild_id in await self.bot.db.list_temp_voice():
            guild = self.bot.get_guild(guild_id)
            canal = guild.get_channel(channel_id) if guild else None

            if canal is None:
                # Já não existe: só limpa o registro.
                await self.bot.db.remove_temp_voice(channel_id)
                removidos += 1
                continue

            if not canal.members:
                await self.apagar(canal, motivo="Órfão de execução anterior")
                removidos += 1

        if removidos:
            log.info("Varredura inicial removeu %d canal(is) temporário(s)", removidos)
        return removidos

    async def apagar(self, canal: discord.VoiceChannel, *, motivo: str) -> None:
        await self.bot.db.remove_temp_voice(canal.id)
        try:
            await canal.delete(reason=motivo)
        except discord.NotFound:
            pass  # alguém apagou antes — o registro já saiu
        except discord.Forbidden:
            log.warning("Sem permissão para apagar o canal %s", canal.id)

    # ------------------------------------------------------------------
    # Eventos de voz
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        antes: discord.VoiceState,
        depois: discord.VoiceState,
    ) -> None:
        if membro.bot:
            return

        # Saída primeiro: mover-se do canal temporário para o saguão dispara
        # os dois ramos, e apagar o canal vazio antes de criar o novo mantém
        # a contagem correta.
        if antes.channel is not None and antes.channel != depois.channel:
            await self.ao_sair(antes.channel)

        if depois.channel is not None and antes.channel != depois.channel:
            await self.ao_entrar(membro, depois.channel)

    async def ao_sair(self, canal: discord.abc.GuildChannel) -> None:
        if canal.members:
            return
        if not await self.bot.db.is_temp_voice(canal.id):
            return
        await self.apagar(canal, motivo="Canal temporário vazio")

    async def ao_entrar(
        self, membro: discord.Member, canal: discord.abc.GuildChannel
    ) -> None:
        cfg = await self.bot.db.get_config(membro.guild.id)
        if cfg.voice_lobby_id != canal.id:
            return

        existente = await self.bot.db.temp_voice_of_owner(membro.guild.id, membro.id)
        if existente is not None:
            atual = membro.guild.get_channel(existente)
            if atual is not None:
                await self.mover(membro, atual)
                return
            # Registro apontando para canal apagado à mão.
            await self.bot.db.remove_temp_voice(existente)

        novo = await self.criar(membro, cfg)
        if novo is not None:
            await self.mover(membro, novo)

    async def criar(
        self, membro: discord.Member, cfg
    ) -> discord.VoiceChannel | None:
        guild = membro.guild
        categoria = (
            guild.get_channel(cfg.voice_category_id)
            if cfg.voice_category_id
            else guild.get_channel(cfg.voice_lobby_id).category
        )

        abertos = await self.bot.db.count_temp_voice(guild.id)
        if abertos >= cfg.voice_max_channels:
            log.info("Limite de canais temporários atingido na guild %s", guild.id)
            return None
        if categoria_cheia(categoria):
            log.warning(
                "Categoria cheia na guild %s (%d canais)", guild.id, MAX_POR_CATEGORIA
            )
            return None

        # O dono administra o próprio canal pela UI do Discord: renomear,
        # mudar vagas, expulsar. Evita reimplementar isso em botões.
        overwrites = {
            membro: discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                mute_members=True,
                deafen_members=True,
                connect=True,
            )
        }

        try:
            return await guild.create_voice_channel(
                name=nome_do_canal(membro),
                category=categoria,
                user_limit=cfg.voice_user_limit or None,
                overwrites=overwrites,
                reason=f"Canal temporário de {membro}",
            )
        except discord.Forbidden:
            log.warning("Sem permissão para criar canal de voz na guild %s", guild.id)
        except discord.HTTPException:
            log.exception("Falha ao criar canal temporário na guild %s", guild.id)
        return None

    async def mover(
        self, membro: discord.Member, canal: discord.VoiceChannel
    ) -> None:
        try:
            await membro.move_to(canal, reason="Canal temporário")
        except discord.HTTPException:
            # A pessoa pode ter saído da voz entre criar e mover; o canal
            # vazio some no próximo evento de saída ou na varredura.
            log.info("Não consegui mover %s para %s", membro.id, canal.id)
            return
        await self.bot.db.add_temp_voice(
            channel_id=canal.id, guild_id=membro.guild.id, owner_id=membro.id
        )

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    voz = app_commands.Group(
        name="voz",
        description="Canais de voz temporários",
        default_permissions=discord.Permissions(manage_channels=True),
        guild_only=True,
    )

    @voz.command(name="ver", description="Mostra a configuração de voz temporária")
    @is_staff()
    async def ver(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)
        if not cfg.voice_lobby_id:
            await interaction.response.send_message(
                embed=embeds.info(
                    "Voz temporária desativada.\n\n"
                    "Crie um canal de voz para servir de saguão e rode\n"
                    "`/voz configurar saguao:<canal>`.",
                    title="Voz temporária",
                ),
                ephemeral=True,
            )
            return

        abertos = await self.bot.db.count_temp_voice(interaction.guild_id)
        embed = embeds.info("", title="Voz temporária")
        embed.add_field(name="Saguão", value=f"<#{cfg.voice_lobby_id}>", inline=True)
        embed.add_field(
            name="Categoria",
            value=f"<#{cfg.voice_category_id}>" if cfg.voice_category_id else "*a do saguão*",
            inline=True,
        )
        embed.add_field(
            name="Vagas por canal",
            value=str(cfg.voice_user_limit) if cfg.voice_user_limit else "sem limite",
            inline=True,
        )
        embed.add_field(
            name="Canais abertos",
            value=f"{abertos} de {cfg.voice_max_channels}",
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @voz.command(name="configurar", description="Define o saguão e as opções")
    @app_commands.describe(
        saguao="Canal de voz que cria os temporários ao ser acessado",
        categoria="Onde criar. Padrão: a categoria do saguão",
        vagas="Limite de pessoas por canal criado. 0 = sem limite",
        maximo="Máximo de canais temporários simultâneos",
    )
    @is_staff()
    async def configurar(
        self,
        interaction: discord.Interaction,
        saguao: discord.VoiceChannel,
        categoria: discord.CategoryChannel | None = None,
        vagas: app_commands.Range[int, 0, 99] | None = None,
        maximo: app_commands.Range[int, 1, 45] | None = None,
    ) -> None:
        me = interaction.guild.me
        if not me.guild_permissions.manage_channels:
            raise ZenibotError("Preciso da permissão **Gerenciar Canais**.")
        if not me.guild_permissions.move_members:
            raise ZenibotError(
                "Preciso da permissão **Mover Membros** — sem ela consigo criar "
                "o canal, mas não levar a pessoa para dentro dele."
            )

        campos: dict[str, object] = {"voice_lobby_id": saguao.id}
        if categoria is not None:
            campos["voice_category_id"] = categoria.id
        if vagas is not None:
            campos["voice_user_limit"] = vagas
        if maximo is not None:
            campos["voice_max_channels"] = maximo

        await self.bot.db.set_config(interaction.guild_id, **campos)
        cfg = await self.bot.db.get_config(interaction.guild_id)

        await interaction.response.send_message(
            embed=embeds.ok(
                f"Saguão: {saguao.mention}\n"
                f"Quem entrar nele ganha um canal próprio, com "
                f"{'sem limite de' if not cfg.voice_user_limit else str(cfg.voice_user_limit)} "
                f"vagas, até {cfg.voice_max_channels} canais simultâneos.\n\n"
                "O dono do canal pode renomeá-lo e ajustar as vagas pela própria "
                "interface do Discord."
            ),
            ephemeral=True,
        )

    @voz.command(name="desativar", description="Desliga a voz temporária")
    @is_staff()
    async def desativar(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        removidos = 0
        for channel_id, _ in await self.bot.db.list_temp_voice(interaction.guild_id):
            canal = interaction.guild.get_channel(channel_id)
            if canal is None or not canal.members:
                if canal is not None:
                    await self.apagar(canal, motivo="Voz temporária desativada")
                else:
                    await self.bot.db.remove_temp_voice(channel_id)
                removidos += 1

        await self.bot.db.set_config(interaction.guild_id, voice_lobby_id=None)
        await interaction.followup.send(
            embed=embeds.ok(
                f"Voz temporária desativada. {removidos} canal(is) vazio(s) removido(s).\n"
                "Canais ainda em uso continuam até esvaziarem."
            ),
            ephemeral=True,
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(TempVoice(bot))
