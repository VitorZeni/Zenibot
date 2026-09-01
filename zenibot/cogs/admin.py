"""Configuração por servidor e comandos de manutenção."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import INITIAL_COGS, Zenibot
from zenibot.core import embeds
from zenibot.core.checks import is_owner, is_staff

log = logging.getLogger(__name__)


class Admin(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    config = app_commands.Group(
        name="config",
        description="Configuração do Zenibot neste servidor",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @config.command(name="ver", description="Mostra a configuração atual")
    @is_staff()
    async def config_view(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)

        def channel(cid: int | None) -> str:
            return f"<#{cid}>" if cid else "*não definido*"

        def role(rid: int | None) -> str:
            return f"<@&{rid}>" if rid else "*não definido*"

        staff = ", ".join(f"<@&{r}>" for r in cfg.staff_role_ids) or "*apenas Manage Server*"

        embed = embeds.info("", title=f"Configuração · {interaction.guild.name}")
        embed.add_field(name="Canal de logs", value=channel(cfg.log_channel_id), inline=True)
        embed.add_field(name="Canal de alertas", value=channel(cfg.alert_channel_id), inline=True)
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(
            name="Boas-vindas",
            value=("ativado" if cfg.welcome_enabled else "desativado")
            + f" · {channel(cfg.welcome_channel_id)}",
            inline=True,
        )
        embed.add_field(name="Autorole", value=role(cfg.autorole_id), inline=True)
        embed.add_field(
            name="Idade mínima da conta",
            value=f"{cfg.min_account_age_days} dia(s)",
            inline=True,
        )
        embed.add_field(name="Cargos de staff", value=staff, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="canal", description="Define um canal do bot")
    @app_commands.describe(tipo="Qual canal configurar", canal="Canal de destino")
    @app_commands.choices(
        tipo=[
            app_commands.Choice(name="Logs de auditoria", value="log_channel_id"),
            app_commands.Choice(name="Alertas de moderação", value="alert_channel_id"),
            app_commands.Choice(name="Boas-vindas", value="welcome_channel_id"),
        ]
    )
    @is_staff()
    async def config_channel(
        self,
        interaction: discord.Interaction,
        tipo: app_commands.Choice[str],
        canal: discord.TextChannel,
    ) -> None:
        # Checar as permissões AGORA evita 403 silencioso na primeira mensagem.
        perms = canal.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=embeds.error(
                    f"Preciso de **Ver Canal**, **Enviar Mensagens** e "
                    f"**Inserir Links** em {canal.mention}."
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.set_config(interaction.guild_id, **{tipo.value: canal.id})
        await interaction.response.send_message(
            embed=embeds.ok(f"{tipo.name} definido para {canal.mention}."),
            ephemeral=True,
        )

    @config.command(name="boas-vindas", description="Ativa ou desativa as boas-vindas")
    @app_commands.describe(ativado="Ligar ou desligar")
    @is_staff()
    async def config_welcome(self, interaction: discord.Interaction, ativado: bool) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)
        if ativado and not cfg.welcome_channel_id:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Defina o canal antes: `/config canal tipo:Boas-vindas`."
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.set_config(interaction.guild_id, welcome_enabled=ativado)
        await interaction.response.send_message(
            embed=embeds.ok(f"Boas-vindas {'ativadas' if ativado else 'desativadas'}."),
            ephemeral=True,
        )

    @config.command(name="autorole", description="Cargo dado automaticamente na entrada")
    @app_commands.describe(cargo="Deixe vazio para desativar")
    @is_staff()
    async def config_autorole(
        self, interaction: discord.Interaction, cargo: discord.Role | None = None
    ) -> None:
        if cargo is not None:
            if cargo.is_bot_managed() or cargo.is_premium_subscriber():
                await interaction.response.send_message(
                    embed=embeds.error("Este cargo é gerenciado pelo Discord e não pode ser dado."),
                    ephemeral=True,
                )
                return
            # Hierarquia: só consigo atribuir cargos abaixo do meu.
            if cargo >= interaction.guild.me.top_role:
                await interaction.response.send_message(
                    embed=embeds.error(
                        f"{cargo.mention} está acima do meu cargo mais alto. "
                        "Mova o cargo do Zenibot para cima na hierarquia."
                    ),
                    ephemeral=True,
                )
                return

        await self.bot.db.set_config(
            interaction.guild_id, autorole_id=cargo.id if cargo else None
        )
        await interaction.response.send_message(
            embed=embeds.ok(
                f"Autorole definido para {cargo.mention}." if cargo else "Autorole desativado."
            ),
            ephemeral=True,
        )

    @config.command(name="idade-minima", description="Sinaliza contas criadas há pouco tempo")
    @app_commands.describe(dias="0 desativa a checagem")
    @is_staff()
    async def config_min_age(
        self, interaction: discord.Interaction, dias: app_commands.Range[int, 0, 365]
    ) -> None:
        await self.bot.db.set_config(interaction.guild_id, min_account_age_days=dias)
        await interaction.response.send_message(
            embed=embeds.ok(
                f"Contas com menos de {dias} dia(s) serão sinalizadas e não recebem autorole."
                if dias
                else "Checagem de idade da conta desativada."
            ),
            ephemeral=True,
        )

    @config.command(name="staff", description="Adiciona ou remove um cargo de staff")
    @app_commands.describe(cargo="Cargo que poderá usar os comandos de moderação")
    @is_staff()
    async def config_staff(self, interaction: discord.Interaction, cargo: discord.Role) -> None:
        cfg = await self.bot.db.get_config(interaction.guild_id)
        roles = list(cfg.staff_role_ids)
        if cargo.id in roles:
            roles.remove(cargo.id)
            texto = f"{cargo.mention} removido dos cargos de staff."
        else:
            roles.append(cargo.id)
            texto = f"{cargo.mention} adicionado aos cargos de staff."

        await self.bot.db.set_config(interaction.guild_id, staff_role_ids=roles)
        await interaction.response.send_message(embed=embeds.ok(texto), ephemeral=True)

    # ------------------------------------------------------------------
    # Manutenção (donos do bot)
    # ------------------------------------------------------------------

    @app_commands.command(name="ping", description="Latência do bot")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=embeds.info(f"Gateway: **{self.bot.latency * 1000:.0f}ms**"),
            ephemeral=True,
        )

    @app_commands.command(name="sync", description="[dono] Registra os slash commands")
    @app_commands.describe(global_="Registrar globalmente (propagação lenta)")
    @is_owner()
    async def sync(self, interaction: discord.Interaction, global_: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        if global_:
            synced = await self.bot.tree.sync()
            escopo = "globalmente"
            # Registrar nos dois escopos duplica cada comando na lista do
            # cliente, sem erro nenhum — falha silenciosa clássica.
            no_outro = len(await self.bot.tree.fetch_commands(guild=interaction.guild))
            aviso = (
                f"\n\n⚠️ Há {no_outro} comando(s) também registrado(s) neste "
                "servidor. Comandos nos dois escopos aparecem **duplicados**. "
                "Limpe um dos lados com `scripts/sync_commands.py --clear`."
                if no_outro
                else ""
            )
        else:
            self.bot.tree.copy_global_to(guild=interaction.guild)
            synced = await self.bot.tree.sync(guild=interaction.guild)
            escopo = "neste servidor"
            no_outro = len(await self.bot.tree.fetch_commands())
            aviso = (
                f"\n\n⚠️ Há {no_outro} comando(s) também registrado(s) "
                "globalmente. Comandos nos dois escopos aparecem "
                "**duplicados**. Limpe os globais com "
                "`scripts/sync_commands.py --global --clear`."
                if no_outro
                else ""
            )

        await interaction.followup.send(
            embed=embeds.ok(f"{len(synced)} comando(s) sincronizado(s) {escopo}.{aviso}"),
            ephemeral=True,
        )

    @app_commands.command(name="reload", description="[dono] Recarrega um módulo")
    @app_commands.describe(cog="Módulo a recarregar")
    @app_commands.choices(
        cog=[app_commands.Choice(name=c.split(".")[-1], value=c) for c in INITIAL_COGS]
    )
    @is_owner()
    async def reload(
        self, interaction: discord.Interaction, cog: app_commands.Choice[str]
    ) -> None:
        await self.bot.reload_extension(cog.value)
        await interaction.response.send_message(
            embed=embeds.ok(f"`{cog.value}` recarregado."), ephemeral=True
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Admin(bot))
