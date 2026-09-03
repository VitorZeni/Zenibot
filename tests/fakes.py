"""Dublês de Discord para testar callbacks de componente.

O `dpytest` não cobre interação nenhuma, então botões, selects e modais só
seriam exercitados no servidor. Estes dublês fecham essa lacuna.

Princípio: **banco real, Discord falso.** As fixtures `db` e `bot` continuam
usando SQLite de verdade; só a camada que fala com a API do Discord é
substituída. Assim o teste exercita a lógica real e observa o que o bot
teria enviado.

Os objetos são classes, e não `SimpleNamespace`, porque overwrites e coleções
de cargo exigem hashabilidade — e porque a comparação de hierarquia
(`cargo >= me.top_role`) precisa de `__ge__` de verdade.
"""

from __future__ import annotations

import discord


class _Avatar:
    url = "https://cdn.discordapp.com/avatar.png"


class _Resposta:
    """Mínimo que `discord.Forbidden` exige para ser construído."""

    status = 403
    reason = "Forbidden"


class FakeRole:
    """Cargo com posição, para as checagens de hierarquia funcionarem."""

    def __init__(
        self,
        id: int = 1,
        name: str = "Cargo",
        position: int = 1,
        *,
        managed: bool = False,
        default: bool = False,
        colour: int = 0,
        hoist: bool = False,
        mentionable: bool = False,
        members: list | None = None,
        **perms,
    ) -> None:
        self.id = id
        self.name = name
        self.position = position
        self.mention = f"<@&{id}>"
        self.permissions = discord.Permissions(**perms)
        self.colour = discord.Colour(colour)
        self.hoist = hoist
        self.mentionable = mentionable
        self.members = members or []
        self._managed = managed
        self._default = default

    @property
    def color(self) -> discord.Colour:
        return self.colour

    async def edit(self, *, reason: str | None = None, **campos) -> FakeRole:
        """Aplica os campos e devolve a si mesmo, como o `Role.edit` real."""
        for chave, valor in campos.items():
            setattr(self, "colour" if chave == "color" else chave, valor)
        return self

    async def delete(self, *, reason: str | None = None) -> None:
        self.apagado = reason

    def is_bot_managed(self) -> bool:
        return self._managed

    def is_integration(self) -> bool:
        return False

    def is_premium_subscriber(self) -> bool:
        return False

    def is_default(self) -> bool:
        return self._default

    # Hierarquia: é o que `assert_can_moderate` e os self-roles comparam.
    def __ge__(self, outro: FakeRole) -> bool:
        return self.position >= outro.position

    def __gt__(self, outro: FakeRole) -> bool:
        return self.position > outro.position

    def __lt__(self, outro: FakeRole) -> bool:
        return self.position < outro.position

    def __repr__(self) -> str:
        return f"<FakeRole {self.name} pos={self.position}>"


class FakeMember:
    """Membro que registra os cargos concedidos e removidos."""

    def __init__(
        self,
        id: int = 100,
        name: str = "membro",
        roles: list[FakeRole] | None = None,
        *,
        dm_fechada: bool = False,
        **perms,
    ) -> None:
        self.id = id
        self.name = name
        self.dm_fechada = dm_fechada
        self.mention = f"<@{id}>"
        self.roles = roles or []
        self.guild_permissions = discord.Permissions(**perms)
        self.guild: FakeGuild | None = None
        self.concedidos: list[FakeRole] = []
        self.removidos: list[FakeRole] = []
        # Registro das ações de moderação sofridas, para os testes olharem.
        self.dms: list[dict] = []
        self.silenciado: tuple | None = None
        self.expulso: str | None = None
        self.display_avatar = _Avatar()
        self.bot = False

    async def send(self, content=None, **kwargs):
        """DM. Em produção pode falhar com DMs fechadas — ver `dm_fechada`."""
        if self.dm_fechada:
            raise discord.Forbidden(_Resposta(), "cannot send to this user")
        self.dms.append({"content": content, **kwargs})
        return FakeMessage()

    async def timeout(self, ate, *, reason: str | None = None) -> None:
        self.silenciado = (ate, reason)

    async def kick(self, *, reason: str | None = None) -> None:
        self.expulso = reason

    @property
    def top_role(self) -> FakeRole:
        return max(self.roles, key=lambda r: r.position, default=FakeRole(position=0))

    async def add_roles(self, *cargos: FakeRole, reason: str | None = None) -> None:
        for cargo in cargos:
            self.concedidos.append(cargo)
            if cargo not in self.roles:
                self.roles.append(cargo)

    async def remove_roles(self, *cargos: FakeRole, reason: str | None = None) -> None:
        for cargo in cargos:
            self.removidos.append(cargo)
            if cargo in self.roles:
                self.roles.remove(cargo)

    def __repr__(self) -> str:
        return f"<FakeMember {self.name}>"


class FakeChannel:
    """Canal que guarda o que foi enviado, em vez de enviar."""

    def __init__(self, id: int = 10, name: str = "geral", **perms) -> None:
        self.id = id
        self.name = name
        self.mention = f"<#{id}>"
        self._perms = discord.Permissions(**perms) if perms else discord.Permissions.all()
        self.enviadas: list[dict] = []
        self.guild: FakeGuild | None = None

    def permissions_for(self, _membro) -> discord.Permissions:
        return self._perms

    async def send(self, content=None, **kwargs):
        self.enviadas.append({"content": content, **kwargs})
        return FakeMessage(id=999, channel=self)


class FakeMessage:
    def __init__(self, id: int = 999, channel: FakeChannel | None = None) -> None:
        self.id = id
        self.channel = channel
        self.jump_url = f"https://discord.com/channels/1/1/{id}"
        self.editada: dict | None = None

    async def edit(self, **kwargs) -> None:
        self.editada = kwargs


class FakeGuild:
    def __init__(
        self,
        id: int = 42,
        *,
        me: FakeMember | None = None,
        roles: list[FakeRole] | None = None,
        canais: list[FakeChannel] | None = None,
        owner_id: int = 1,
    ) -> None:
        self.id = id
        self.name = "Servidor de Teste"
        self.owner_id = owner_id
        # O bot nasce alto na hierarquia: o caso comum é ele poder agir, e os
        # testes que precisam do contrário abaixam o cargo dele de propósito.
        self.me = me or FakeMember(
            id=999, name="Zenibot", roles=[FakeRole(900, "Zenibot", 100)],
            manage_roles=True, manage_channels=True, manage_guild=True,
        )
        self.me.guild = self
        self.default_role = FakeRole(id, "@everyone", 0, default=True)
        self._roles = {r.id: r for r in (roles or [])}
        self._canais = {c.id: c for c in (canais or [])}
        for canal in self._canais.values():
            canal.guild = self
        self._membros: dict[int, FakeMember] = {}
        self.roles = list(roles or [])
        self.banidos: list[tuple] = []
        self.desbanidos: list[int] = []

    async def create_role(self, *, name: str, **kwargs) -> FakeRole:
        cargo = FakeRole(
            id=1000 + len(self._roles),
            name=name,
            position=1,
        )
        cargo.criado_com = kwargs
        self._roles[cargo.id] = cargo
        self.roles.append(cargo)
        return cargo

    async def ban(self, alvo, *, reason=None, delete_message_seconds=0) -> None:
        self.banidos.append((alvo, reason, delete_message_seconds))

    async def unban(self, alvo, *, reason=None) -> None:
        self.desbanidos.append(getattr(alvo, "id", alvo))

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)

    def get_member(self, membro_id: int) -> FakeMember | None:
        return self._membros.get(membro_id)

    def adiciona_membro(self, membro: FakeMember) -> FakeMember:
        membro.guild = self
        self._membros[membro.id] = membro
        return membro

    def get_channel(self, canal_id: int) -> FakeChannel | None:
        return self._canais.get(canal_id)

    def adiciona_cargo(self, cargo: FakeRole) -> FakeRole:
        self._roles[cargo.id] = cargo
        return cargo

    def adiciona_canal(self, canal: FakeChannel) -> FakeChannel:
        canal.guild = self
        self._canais[canal.id] = canal
        return canal


class FakeResponse:
    """Metade `InteractionResponse` que os callbacks realmente usam."""

    def __init__(self, done: bool = False) -> None:
        self._done = done
        self.enviado: dict | None = None
        self.editado: dict | None = None
        self.modal = None

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content=None, **kwargs) -> None:
        self.enviado = {"content": content, **kwargs}
        self._done = True

    async def edit_message(self, content=None, **kwargs) -> None:
        self.editado = {"content": content, **kwargs}
        self._done = True

    async def send_modal(self, modal) -> None:
        self.modal = modal
        self._done = True

    async def defer(self, **kwargs) -> None:
        self._done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.enviado: dict | None = None

    async def send(self, content=None, **kwargs):
        self.enviado = {"content": content, **kwargs}
        return FakeMessage()


class FakeInteraction:
    """Interação com cliente real: `interaction.client.db` funciona.

    É o que permite testar guardas que consultam configuração ou histórico
    sem simular o banco.
    """

    def __init__(
        self,
        *,
        client=None,
        user: FakeMember | None = None,
        guild: FakeGuild | None = None,
        channel: FakeChannel | None = None,
        done: bool = False,
    ) -> None:
        self.client = client
        self.guild = guild or FakeGuild()
        self.user = user or FakeMember()
        self.user.guild = self.guild
        self.guild_id = self.guild.id
        self.channel = channel or FakeChannel()
        self.response = FakeResponse(done)
        self.followup = FakeFollowup()
        self.message = FakeMessage()

    # -- observação do resultado -------------------------------------------

    @property
    def resposta(self) -> dict | None:
        """O que o usuário recebeu, venha de onde vier."""
        return (
            self.response.enviado
            or self.response.editado
            or self.followup.enviado
        )

    @property
    def texto(self) -> str:
        """Descrição do embed respondido — onde as mensagens de erro moram."""
        resposta = self.resposta
        if not resposta or "embed" not in resposta:
            return ""
        return resposta["embed"].description or ""

    @property
    def respondeu(self) -> bool:
        """Falso aqui significa 'o aplicativo não respondeu a tempo'."""
        return self.resposta is not None
