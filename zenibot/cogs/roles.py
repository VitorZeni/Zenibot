"""Criação e edição de cargos.

O cargo nasce **sem permissão nenhuma** — ver `core/roles.py` para o porquê.
O que o comando entrega é o resto: nome, cor, destaque na lista de membros e
se pode ser mencionado.

Apagar exige confirmação. Não há como desfazer: quem tinha o cargo perde o
acesso que ele dava, e o Discord não guarda quem eram.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands, ui
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import colors, embeds, roles
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.errors import respond_error

log = logging.getLogger(__name__)

TIMEOUT_CONFIRMACAO = 60


def parse_cor_ou_erro(valor: str) -> discord.Colour | None:
    try:
        return colors.parse_cor(valor)
    except ValueError as exc:
        raise ZenibotError(
            f"Cor inválida: `{valor}`. Use um hex como `#5865F2` ou um nome "
            "como `vermelho`."
        ) from exc


def ficha(cargo: discord.Role) -> discord.Embed:
    embed = embeds.ok(
        f"{cargo.mention}\n`{cargo.id}`", title=f"Cargo · {cargo.name}"
    )
    embed.colour = cargo.colour if cargo.colour.value else embeds.COLOR_OK
    embed.add_field(
        name="Cor",
        value=str(cargo.colour) if cargo.colour.value else "*sem cor*",
        inline=True,
    )
    embed.add_field(
        name="Destacado", value="sim" if cargo.hoist else "não", inline=True
    )
    embed.add_field(
        name="Mencionável", value="sim" if cargo.mentionable else "não", inline=True
    )
    embed.add_field(
        name="Permissões", value=roles.descreve_permissoes(cargo), inline=False
    )
    return embed


class ConfirmarExclusao(ui.View):
    """Apagar cargo é irreversível — quem o tinha perde o acesso."""

    def __init__(self, autor_id: int, cargo: discord.Role) -> None:
        super().__init__(timeout=TIMEOUT_CONFIRMACAO)
        self.autor_id = autor_id
        self.cargo = cargo

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                embed=embeds.error("Esta confirmação é de outra pessoa."),
                ephemeral=True,
            )
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: ui.Item, /
    ) -> None:
        await respond_error(interaction, error, contexto="confirmação de exclusão")

    @ui.button(label="Apagar mesmo assim", style=discord.ButtonStyle.danger)
    async def confirmar(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        nome, quantos = self.cargo.name, len(self.cargo.members)
        try:
            await self.cargo.delete(reason=f"Apagado por {interaction.user}")
        except discord.Forbidden:
            raise ZenibotError("Não tenho permissão para apagar esse cargo.") from None

        await interaction.response.edit_message(
            embed=embeds.ok(
                f"Cargo **{nome}** apagado. {quantos} membro(s) perderam ele."
            ),
            view=None,
        )
        self.stop()

    @ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        await interaction.response.edit_message(
            embed=embeds.info("Nada foi apagado."), view=None
        )
        self.stop()


class Roles(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    cargo = app_commands.Group(
        name="cargo",
        description="Criação e edição de cargos",
        default_permissions=discord.Permissions(manage_roles=True),
        guild_only=True,
    )

    @cargo.command(name="criar", description="Cria um cargo sem permissões")
    @app_commands.describe(
        nome="Nome do cargo",
        cor="Hex como #5865F2 ou nome como vermelho",
        destacado="Mostrar separado na lista de membros",
        mencionavel="Permitir que qualquer um mencione o cargo",
    )
    @is_staff()
    async def criar(
        self,
        interaction: discord.Interaction,
        nome: app_commands.Range[str, 1, roles.LIMITE_NOME],
        cor: str | None = None,
        destacado: bool = False,
        mencionavel: bool = False,
    ) -> None:
        guild = interaction.guild
        roles.assert_pode_criar(guild)
        nome = roles.validar_nome(nome)
        cor_final = parse_cor_ou_erro(cor) if cor else None

        try:
            novo = await guild.create_role(
                name=nome,
                colour=cor_final or discord.Colour.default(),
                hoist=destacado,
                mentionable=mencionavel,
                # Sem permissão nenhuma, de propósito.
                permissions=discord.Permissions.none(),
                reason=f"Criado por {interaction.user}",
            )
        except discord.Forbidden:
            raise ZenibotError("Não tenho permissão para criar cargos.") from None

        embed = ficha(novo)
        embed.set_footer(
            text="Nasce sem permissões. Conceda acesso a canais com /canal acesso, "
            "ou permissões de servidor pelas configurações do Discord."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cargo.command(name="editar", description="Altera nome, cor ou visibilidade")
    @app_commands.describe(
        cargo="Cargo a alterar",
        nome="Novo nome",
        cor="Novo hex ou nome de cor. Use `nenhuma` para remover",
        destacado="Mostrar separado na lista de membros",
        mencionavel="Permitir que qualquer um mencione o cargo",
    )
    @is_staff()
    async def editar(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
        nome: app_commands.Range[str, 1, roles.LIMITE_NOME] | None = None,
        cor: str | None = None,
        destacado: bool | None = None,
        mencionavel: bool | None = None,
    ) -> None:
        roles.assert_gerenciavel(cargo, interaction.user, interaction.guild.me)

        campos: dict = {}
        if nome is not None:
            campos["name"] = roles.validar_nome(nome)
        if cor is not None:
            if cor.strip().lower() in ("nenhuma", "nenhum", "sem", "remover"):
                escolhida = None
            else:
                escolhida = parse_cor_ou_erro(cor)
            # `Colour.default()` é o zero do Discord — o mesmo que "sem cor".
            campos["colour"] = escolhida or discord.Colour.default()
        if destacado is not None:
            campos["hoist"] = destacado
        if mencionavel is not None:
            campos["mentionable"] = mencionavel

        if not campos:
            raise ZenibotError(
                "Informe ao menos um campo para alterar: nome, cor, destacado "
                "ou mencionavel."
            )

        try:
            atualizado = await cargo.edit(
                **campos, reason=f"Editado por {interaction.user}"
            )
        except discord.Forbidden:
            raise ZenibotError("Não tenho permissão para editar esse cargo.") from None

        # `edit` devolve o cargo atualizado; o objeto original ainda carrega os
        # valores antigos até o cache receber o evento.
        await interaction.response.send_message(
            embed=ficha(atualizado or cargo), ephemeral=True
        )

    @cargo.command(name="apagar", description="Apaga um cargo, com confirmação")
    @app_commands.describe(cargo="Cargo a apagar")
    @is_staff()
    async def apagar(
        self, interaction: discord.Interaction, cargo: discord.Role
    ) -> None:
        roles.assert_gerenciavel(cargo, interaction.user, interaction.guild.me)

        quantos = len(cargo.members)
        aviso = embeds.warn(
            f"Apagar **{cargo.name}**?\n\n"
            f"**{quantos}** membro(s) têm este cargo e perderiam o acesso que "
            f"ele dá.\nConcede: {roles.descreve_permissoes(cargo)}.\n\n"
            "Não há como desfazer — o Discord não guarda quem tinha o cargo.",
            title="Confirmar exclusão",
        )
        await interaction.response.send_message(
            embed=aviso,
            view=ConfirmarExclusao(interaction.user.id, cargo),
            ephemeral=True,
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(Roles(bot))
