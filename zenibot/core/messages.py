"""Referências a mensagens e validação de URL.

Extraído porque `selfroles` e `templates` tinham cada um a sua cópia desta
lógica — ~35 linhas idênticas que já haviam começado a divergir no texto das
mensagens de erro.
"""

from __future__ import annotations

import re

import discord

from zenibot.core.checks import ZenibotError

MESSAGE_LINK = re.compile(
    r"https?://(?:\w+\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild>\d+)/(?P<channel>\d+)/(?P<message>\d+)"
)


def url_valida(valor: str) -> bool:
    """URL sem esquema http(s) faz o Discord recusar a publicação com 400."""
    return valor.startswith(("http://", "https://"))


async def resolver_mensagem(
    interaction: discord.Interaction,
    referencia: str,
    *,
    descricao: str = "mensagem",
    apenas_do_bot: bool = True,
) -> discord.Message:
    """Resolve um ID ou link de mensagem para o objeto correspondente.

    O ID sozinho assume o canal atual; o link completo carrega o canal, e é
    por isso que ele é a saída quando a mensagem está em outro lugar.
    """
    referencia = referencia.strip()
    canal: discord.abc.Messageable | None = interaction.channel

    link = MESSAGE_LINK.search(referencia)
    if link:
        if int(link["guild"]) != interaction.guild_id:
            raise ZenibotError("Esse link aponta para outro servidor.")
        canal = interaction.guild.get_channel(int(link["channel"]))
        message_id = int(link["message"])
    else:
        if not referencia.isdigit():
            raise ZenibotError(
                f"Informe o ID da {descricao} ou o link dela (botão direito na "
                "mensagem > Copiar link da mensagem)."
            )
        message_id = int(referencia)

    if canal is None:
        raise ZenibotError("Não encontrei o canal dessa mensagem.")

    try:
        alvo = await canal.fetch_message(message_id)
    except discord.NotFound as exc:
        raise ZenibotError(
            f"{descricao.capitalize()} não encontrada. Se ela está em outro "
            "canal, use o link completo em vez do ID."
        ) from exc
    except discord.Forbidden as exc:
        raise ZenibotError("Não tenho acesso a esse canal.") from exc

    # A API do Discord não permite editar mensagem de terceiros, então checar
    # aqui dá um erro claro em vez de um 403 mais adiante.
    if apenas_do_bot and alvo.author.id != interaction.client.user.id:
        raise ZenibotError("Essa mensagem não foi enviada por mim.")
    return alvo
