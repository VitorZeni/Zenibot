"""Painéis de cargos auto-atribuíveis com botões persistentes.

Persistência sem estado em memória
----------------------------------
Uma `View` comum morre no restart: os botões viram enfeite. A abordagem
tradicional é guardar cada painel no banco e re-registrar uma View por painel
no boot — o que custa uma consulta na partida e N views residentes.

Aqui usamos `discord.ui.DynamicItem`: o botão se reconstrói a partir do
próprio `custom_id` da mensagem (`srole:<role_id>`), casado por regex. Basta
um `add_dynamic_items()` no setup_hook e qualquer painel, de qualquer época,
volta a responder. Nenhuma tabela, nenhuma view por painel.

Segurança
---------
Um painel de self-roles entrega cargos sem revisão humana, então um cargo
perigoso ali é uma escalada de privilégio para qualquer membro. As checagens
rodam **duas vezes**: ao montar o painel e de novo a cada clique — as
permissões de um cargo podem mudar depois que o painel foi criado.
"""

from __future__ import annotations

import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from zenibot.bot import Zenibot
from zenibot.core import embeds
from zenibot.core.checks import ZenibotError, is_staff
from zenibot.core.errors import respond_error
from zenibot.core.guild import exigir_publicavel
from zenibot.core.messages import resolver_mensagem

log = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = "srole"

# 5 botões por linha, 5 linhas; deixamos folga para não esbarrar no limite.
MAX_BUTTONS = 20

# Permissões que jamais devem ser auto-atribuíveis: qualquer uma delas
# transforma o painel em porta de entrada para controle do servidor.
DANGEROUS_PERMISSIONS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "manage_nicknames",
    "manage_events",
    "manage_threads",
    "ban_members",
    "kick_members",
    "moderate_members",
    "mention_everyone",
    "view_audit_log",
)

def dangerous_permissions(role: discord.Role) -> list[str]:
    """Lista as permissões perigosas que o cargo concede."""
    perms = role.permissions
    return [name for name in DANGEROUS_PERMISSIONS if getattr(perms, name, False)]


def assert_role_assignable(
    role: discord.Role,
    actor: discord.Member,
    me: discord.Member,
) -> None:
    """Valida que o cargo pode virar botão de self-role.

    Levanta ZenibotError com a explicação, para o handler global exibir.
    """
    if role.is_default():
        raise ZenibotError("O cargo `@everyone` não pode ser atribuído.")
    if role.is_bot_managed() or role.is_integration():
        raise ZenibotError(f"{role.mention} é gerenciado por uma integração e não pode ser dado.")
    if role.is_premium_subscriber():
        raise ZenibotError("O cargo de impulsionador é gerenciado pelo Discord.")

    perigosas = dangerous_permissions(role)
    if perigosas:
        lista = ", ".join(f"`{p}`" for p in perigosas)
        raise ZenibotError(
            f"{role.mention} concede permissões administrativas ({lista}) e "
            "não pode ser auto-atribuível. Qualquer membro poderia clicar no botão."
        )

    if not me.guild_permissions.manage_roles:
        raise ZenibotError("Preciso da permissão **Gerenciar Cargos**.")
    if role >= me.top_role:
        raise ZenibotError(
            f"{role.mention} está acima do meu cargo mais alto. "
            "Mova o cargo do Zenibot para cima na hierarquia."
        )
    # Impede que quem cria o painel distribua cargo acima do próprio nível.
    if actor.id != actor.guild.owner_id and role >= actor.top_role:
        raise ZenibotError(
            f"{role.mention} está no seu nível ou acima dele. "
            "Você não pode tornar auto-atribuível um cargo que não controla."
        )


class ClickCooldown:
    """Cooldown por usuário: um botão público precisa resistir a spam.

    Sem isto, cliques repetidos enfileiram requisições de cargo e queimam o
    rate limit do bot inteiro — não só o do usuário.
    """

    def __init__(self, rate: int, per: float) -> None:
        self._rate = rate
        self._per = per
        self._hits: dict[tuple[int, int], list[float]] = {}

    def clear(self) -> None:
        """Esquece todos os registros. Usado para isolar testes — em produção
        a poda oportunista de `hit` já dá conta."""
        self._hits.clear()

    def hit(self, guild_id: int, user_id: int) -> float | None:
        """None se pode prosseguir; senão, quantos segundos faltam."""
        agora = time.monotonic()
        chave = (guild_id, user_id)
        marcas = [t for t in self._hits.get(chave, ()) if agora - t < self._per]

        if len(marcas) >= self._rate:
            self._hits[chave] = marcas
            return self._per - (agora - marcas[0])

        marcas.append(agora)
        self._hits[chave] = marcas

        if len(self._hits) > 1000:  # poda oportunista
            self._hits = {
                k: v
                for k, v in self._hits.items()
                if any(agora - t < self._per for t in v)
            }
        return None


_cooldown = ClickCooldown(rate=4, per=12.0)


class RoleToggleButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=rf"{CUSTOM_ID_PREFIX}:(?P<role_id>\d+)",
):
    """Botão que alterna um cargo. Reconstruído a partir do custom_id."""

    def __init__(
        self,
        role_id: int,
        *,
        label: str,
        emoji: str | discord.PartialEmoji | None = None,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{CUSTOM_ID_PREFIX}:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> RoleToggleButton:
        # `item` é o botão como veio da mensagem: preserva rótulo e emoji sem
        # precisar consultar banco nenhum.
        return cls(
            int(match["role_id"]),
            label=item.label or "Cargo",
            emoji=item.emoji,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Trata o erro aqui dentro de propósito.

        Um DynamicItem não passa pelo `on_error` de uma View — a própria
        documentação do discord.py diz isso —, então uma exceção escaparia
        para o log e o usuário veria apenas "não respondeu a tempo".
        """
        try:
            await self.toggle(interaction)
        except Exception as exc:  # noqa: BLE001 — o handler classifica
            await respond_error(interaction, exc, contexto="botão de self-role")

    async def toggle(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None:
            raise ZenibotError("Este botão só funciona dentro de um servidor.")

        restante = _cooldown.hit(guild.id, member.id)
        if restante is not None:
            await interaction.response.send_message(
                embed=embeds.warn(f"Devagar — tente de novo em {restante:.0f}s."),
                ephemeral=True,
            )
            return

        role = guild.get_role(self.role_id)
        if role is None:
            raise ZenibotError(
                "Esse cargo não existe mais. Peça a um administrador para "
                "atualizar o painel."
            )

        # Revalidação no clique: as permissões do cargo podem ter mudado
        # depois que o painel foi criado.
        perigosas = dangerous_permissions(role)
        if perigosas:
            log.warning(
                "Botão de self-role bloqueado: cargo %s na guild %s ganhou %s",
                role.id,
                guild.id,
                perigosas,
            )
            raise ZenibotError(
                "Esse cargo passou a conceder permissões administrativas e foi "
                "desativado por segurança. Avise a administração."
            )

        me = guild.me
        if not me.guild_permissions.manage_roles or role >= me.top_role:
            raise ZenibotError(
                "Não consigo gerenciar esse cargo. Verifique minhas permissões "
                "e a posição do meu cargo na hierarquia."
            )

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Self-role (painel)")
                texto = f"Cargo {role.mention} **removido**."
            else:
                await member.add_roles(role, reason="Self-role (painel)")
                texto = f"Cargo {role.mention} **adicionado**."
        except discord.Forbidden as exc:
            raise ZenibotError("Não tenho permissão para alterar esse cargo.") from exc

        await interaction.response.send_message(
            embed=embeds.ok(texto), ephemeral=True
        )


def read_buttons(message: discord.Message) -> list[RoleToggleButton]:
    """Extrai os botões de self-role já presentes na mensagem."""
    botoes: list[RoleToggleButton] = []
    for item in discord.ui.View.from_message(message).children:
        if not isinstance(item, discord.ui.Button) or not item.custom_id:
            continue
        if not item.custom_id.startswith(f"{CUSTOM_ID_PREFIX}:"):
            continue
        try:
            role_id = int(item.custom_id.split(":", 1)[1])
        except ValueError:
            continue
        botoes.append(
            RoleToggleButton(role_id, label=item.label or "Cargo", emoji=item.emoji)
        )
    return botoes


def build_view(botoes: list[RoleToggleButton]) -> discord.ui.View:
    """View sem timeout — botões persistentes não expiram."""
    view = discord.ui.View(timeout=None)
    for botao in botoes:
        view.add_item(botao)
    return view


def panel_embed(
    guild: discord.Guild,
    titulo: str,
    descricao: str | None,
    botoes: list[RoleToggleButton],
) -> discord.Embed:
    embed = embeds.info(descricao or "Clique nos botões para pegar ou remover cargos.",
                        title=titulo)
    if botoes:
        linhas = []
        for botao in botoes:
            role = guild.get_role(botao.role_id)
            alvo = role.mention if role else f"*cargo removido* (`{botao.role_id}`)"
            linhas.append(f"{botao.item.label} — {alvo}")
        embed.add_field(name="Cargos", value="\n".join(linhas)[:1024], inline=False)
    else:
        embed.set_footer(text="Painel vazio — use /painel adicionar")
    return embed


class SelfRoles(commands.Cog):
    def __init__(self, bot: Zenibot) -> None:
        self.bot = bot

    painel = app_commands.Group(
        name="painel",
        description="Painéis de cargos auto-atribuíveis",
        default_permissions=discord.Permissions(manage_roles=True),
        guild_only=True,
    )

    @painel.command(name="criar", description="Cria um painel de cargos vazio")
    @app_commands.describe(
        canal="Onde publicar o painel",
        titulo="Título do painel",
        descricao="Texto explicativo (opcional)",
    )
    @is_staff()
    async def criar(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel,
        titulo: app_commands.Range[str, 1, 256],
        descricao: app_commands.Range[str, 1, 2000] | None = None,
    ) -> None:
        exigir_publicavel(canal, interaction.guild.me)

        await interaction.response.defer(ephemeral=True)
        mensagem = await canal.send(
            embed=panel_embed(interaction.guild, titulo, descricao, []),
            allowed_mentions=embeds.NO_MENTIONS,
        )
        await interaction.followup.send(
            embed=embeds.ok(
                f"Painel criado em {canal.mention}.\n\n"
                f"Adicione cargos com:\n"
                f"`/painel adicionar mensagem:{mensagem.id} cargo:@Cargo`"
            ),
            ephemeral=True,
        )

    @painel.command(name="adicionar", description="Adiciona um botão de cargo ao painel")
    @app_commands.describe(
        mensagem="ID ou link da mensagem do painel",
        cargo="Cargo que o botão vai alternar",
        rotulo="Texto do botão (padrão: nome do cargo)",
        emoji="Emoji do botão (opcional)",
    )
    @is_staff()
    async def adicionar(
        self,
        interaction: discord.Interaction,
        mensagem: str,
        cargo: discord.Role,
        rotulo: app_commands.Range[str, 1, 80] | None = None,
        emoji: str | None = None,
    ) -> None:
        assert_role_assignable(cargo, interaction.user, interaction.guild.me)

        await interaction.response.defer(ephemeral=True)
        alvo = await resolver_mensagem(
            interaction, mensagem, descricao="mensagem do painel"
        )
        botoes = read_buttons(alvo)

        if any(b.role_id == cargo.id for b in botoes):
            raise ZenibotError(f"{cargo.mention} já está nesse painel.")
        if len(botoes) >= MAX_BUTTONS:
            raise ZenibotError(f"Um painel comporta no máximo {MAX_BUTTONS} cargos.")

        emoji_parsed: discord.PartialEmoji | None = None
        if emoji:
            try:
                emoji_parsed = discord.PartialEmoji.from_str(emoji.strip())
            except Exception as exc:  # noqa: BLE001 — entrada livre do usuário
                raise ZenibotError(f"Emoji inválido: `{emoji}`") from exc

        botoes.append(
            RoleToggleButton(cargo.id, label=rotulo or cargo.name, emoji=emoji_parsed)
        )

        titulo = alvo.embeds[0].title if alvo.embeds else "Cargos"
        descricao = alvo.embeds[0].description if alvo.embeds else None
        await alvo.edit(
            embed=panel_embed(interaction.guild, titulo, descricao, botoes),
            view=build_view(botoes),
        )
        await interaction.followup.send(
            embed=embeds.ok(f"{cargo.mention} adicionado ao painel ({len(botoes)}/{MAX_BUTTONS})."),
            ephemeral=True,
        )

    @painel.command(name="remover", description="Remove um botão de cargo do painel")
    @app_commands.describe(
        mensagem="ID ou link da mensagem do painel",
        cargo="Cargo a remover do painel",
    )
    @is_staff()
    async def remover(
        self, interaction: discord.Interaction, mensagem: str, cargo: discord.Role
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        alvo = await resolver_mensagem(
            interaction, mensagem, descricao="mensagem do painel"
        )
        botoes = read_buttons(alvo)

        restantes = [b for b in botoes if b.role_id != cargo.id]
        if len(restantes) == len(botoes):
            raise ZenibotError(f"{cargo.mention} não está nesse painel.")

        titulo = alvo.embeds[0].title if alvo.embeds else "Cargos"
        descricao = alvo.embeds[0].description if alvo.embeds else None
        await alvo.edit(
            embed=panel_embed(interaction.guild, titulo, descricao, restantes),
            view=build_view(restantes) if restantes else None,
        )
        await interaction.followup.send(
            embed=embeds.ok(f"{cargo.mention} removido do painel."), ephemeral=True
        )


async def setup(bot: Zenibot) -> None:
    await bot.add_cog(SelfRoles(bot))
    # É isto que ressuscita os painéis antigos após um restart: o bot passa a
    # reconhecer qualquer custom_id que case com o template de RoleToggleButton,
    # sem precisar saber que painéis existem. Registrar aqui (e não em bot.py)
    # evita o import circular — o cog já importa Zenibot.
    # A chamada é idempotente: o registro é indexado pelo template compilado,
    # então um /reload não duplica nada.
    bot.add_dynamic_items(RoleToggleButton)
