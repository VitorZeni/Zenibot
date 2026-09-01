# Guia de Criação de Bots para Discord: Gerenciamento e Eventos

> Documento técnico de referência — compilado a partir da documentação oficial do Discord (`docs.discord.com`), das documentações do discord.js v14 e discord.py 2.x, e de práticas consolidadas de arquitetura de aplicações orientadas a eventos.
> Última verificação das fontes: **agosto de 2026**.

---

## 1. Visão Geral & Boas Práticas da API

### 1.1. O modelo de comunicação do Discord

Um bot do Discord não é um programa monolítico: ele opera sobre **dois canais de comunicação distintos e complementares**.

| Canal | Direção | Protocolo | Uso |
|---|---|---|---|
| **Gateway** | Discord → Bot | WebSocket persistente | Recebimento de eventos em tempo real (`MESSAGE_CREATE`, `GUILD_MEMBER_ADD`, `INTERACTION_CREATE`…) |
| **REST API** | Bot → Discord | HTTPS (`/api/v10`) | Execução de ações (enviar mensagem, banir membro, criar canal, responder interação) |

Consequência arquitetural fundamental: **você escuta pelo Gateway, mas age pela REST**. Mesmo respostas a interações recebidas via Gateway são enviadas por requisições HTTP — a documentação é explícita: *"Responses to Interactions are not sent as commands over the gateway"*.

Existe ainda um terceiro modelo, o **Interactions Endpoint URL (HTTP Interactions)**: em vez de manter um WebSocket, o Discord envia as interações via POST para uma URL sua. É mutuamente exclusivo com o recebimento de `INTERACTION_CREATE` pelo Gateway. Vantagem: permite arquitetura *serverless* (Cloudflare Workers, Lambda) e escala horizontalmente sem sharding. Desvantagem: você perde todos os demais eventos do Gateway — inviável para bots de moderação e logging.

**Recomendação para bots de gerenciamento de servidor:** use o Gateway. O modelo HTTP só faz sentido para bots puramente reativos a slash commands.

### 1.2. O ciclo de vida da conexão Gateway

```
GET /gateway/bot  →  { url, shards, session_start_limit }
      ↓
Conectar WebSocket (wss://…?v=10&encoding=json)
      ↓
Receber HELLO (op 10)  →  iniciar heartbeat no heartbeat_interval
      ↓
Enviar IDENTIFY (op 2) com token + intents + shard
      ↓
Receber READY  →  guardar session_id e resume_gateway_url
      ↓
Fluxo de eventos (op 0), cada um com um seq incremental
      ↓
Queda de conexão → RESUME (op 6) usando resume_gateway_url + session_id + seq
      ↓
Se RESUME falhar (INVALID_SESSION d:false) → reconectar e IDENTIFY novamente
```

Bibliotecas maduras (discord.js, discord.py, JDA) implementam heartbeat, resume e backoff automaticamente. **Nunca reimplemente isso manualmente** — erros aqui queimam seu limite de `IDENTIFY`.

### 1.3. Limites operacionais que definem a arquitetura

Estes números não são detalhes: eles determinam decisões de design.

**Gateway:**
- **1.000 `IDENTIFY` por 24 horas** por aplicação (somando todos os shards). Estourar resulta em encerramento da sessão, **reset automático do token** e notificação ao dono. Um loop de crash-restart mal tratado pode derrubar seu bot por um dia inteiro.
- **120 eventos enviados por conexão a cada 60 segundos** (~2/s). Exceder causa desconexão imediata.
- Startup concorrente de shards limitado por `max_concurrency` (do objeto `session_start_limit`), em janelas de 5 segundos.
- **Sharding obrigatório a partir de 2.500 guilds.** Fórmula de roteamento: `shard_id = (guild_id >> 22) % num_shards`. Eventos não-guild (DMs) sempre chegam no shard 0.

**REST:**
- **Limite global: 50 requisições/segundo** por bot. Endpoints de interação são isentos.
- **Buckets por rota**, identificados pelo header `X-RateLimit-Bucket`. Recursos de topo (`guild_id`, `channel_id`, `webhook_id`) têm buckets independentes — atingir o limite numa guild não afeta outra.
- Headers a respeitar dinamicamente: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `X-RateLimit-Reset-After`, `X-RateLimit-Scope`.
- Em `429`, use **sempre** o campo `retry_after` / header `Retry-After`. **Nunca hardcode limites** — eles mudam sem aviso.
- **10.000 requisições inválidas (401/403/429) em 10 minutos → ban temporário de IP pela Cloudflare.** Isso é a causa mais comum de "meu bot parou do nada" em bots que fazem *permission checks* por tentativa e erro.

**Interações:**
- **3 segundos** para o ACK inicial. Perdido o prazo, o token da interação é invalidado permanentemente.
- **15 minutos** de validade do token para follow-ups após o ACK.

### 1.4. Boas práticas transversais

1. **Defira cedo.** Qualquer handler que toque banco de dados, API externa ou faça mais de uma chamada REST deve chamar `deferReply()` / `interaction.response.defer()` como primeira instrução.
2. **Cheque permissões antes de agir**, não depois de receber 403 — isso protege você do limite de requisições inválidas.
3. **Use `allowed_mentions` explicitamente** em toda mensagem que ecoe conteúdo de usuário. Sem isso, um bot de logs vira vetor de mass-ping.
4. **Habilite apenas os intents necessários.** Menos eventos = menos banda, menos cache, menos superfície de risco.
5. **Sempre envie `X-Audit-Log-Reason`** em ações moderativas (a biblioteca expõe isso como parâmetro `reason`).
6. **Idempotência**: eventos do Gateway podem ser reentregues após um `RESUME`. Ações com efeito colateral devem tolerar reprocessamento.

---

## 2. Tecnologias & Stacks Recomendadas

### 2.1. Comparativo: Discord.js vs. discord.py

| Critério | **discord.js** (v14.27+) | **discord.py** (v2.7+) |
|---|---|---|
| Runtime | Node.js ≥ 18 | Python ≥ 3.8 |
| Modelo de concorrência | Event loop nativo, callbacks/Promises | `asyncio`, `async/await` |
| Organização de código | Handlers manuais (`events/`, `commands/`) — a lib não impõe estrutura | **Cogs + Extensions** — sistema de módulos nativo com hot-reload |
| Slash commands | `SlashCommandBuilder` + deploy script separado | Decoradores `@app_commands.command` + `tree.sync()` |
| Agendamento nativo | ❌ (usar `node-cron`, BullMQ, `setTimeout`) | ✅ **`discord.ext.tasks`** (`@tasks.loop`) |
| Tipagem | TypeScript de primeira classe (tipos oficiais) | Type hints completos, ótimo suporte a Pyright |
| Sharding | `ShardingManager` embutido (`totalShards: 'auto'`) | `AutoShardedBot` embutido |
| Ecossistema de hosting | Excelente (Docker, PaaS, edge) | Excelente, ligeiramente mais pesado em memória |
| Curva de aprendizado | Média — exige montar a arquitetura | Baixa — estrutura vem pronta |
| Ecossistema adjacente | Maior volume de bibliotecas para áudio/música (`@discordjs/voice`) | Superior para integração com ML/dados (pandas, scikit) |

**Veredito prático:**

- **Escolha discord.js** se: a equipe já é JavaScript/TypeScript; o bot terá dashboard web (compartilhando tipos e código); você precisa de voz/música; você quer escala grande com sharding e microserviços.
- **Escolha discord.py** se: a prioridade é velocidade de desenvolvimento e organização; o bot terá muitas rotinas agendadas (o `tasks` nativo elimina uma dependência inteira); há integração com processamento de dados/IA.
- **JDA (Java)** é a terceira opção séria: performance e tipagem fortíssimas, ideal para bots de altíssimo volume em ambientes corporativos JVM, mas com verbosidade e ciclo de desenvolvimento mais lentos.

Para o **Zenibot**, com foco declarado em *gerenciamento de servidor* + *eventos e agendamentos*: **discord.py é a escolha de melhor custo-benefício** — o `discord.ext.tasks` e o sistema de Cogs cobrem exatamente os dois eixos do projeto sem infraestrutura adicional. Se houver intenção de dashboard web ou funcionalidade de voz, discord.js + TypeScript passa a ser preferível.

### 2.2. Stack de referência sugerida

```
Linguagem      → Python 3.12  (ou TypeScript 5.x / Node 20 LTS)
Biblioteca     → discord.py 2.7.x  (ou discord.js 14.27.x)
Persistência   → PostgreSQL (estado relacional: configs, casos de moderação, agendamentos)
Cache/Filas    → Redis (rate-limit distribuído, locks, fila de jobs)
Agendamento    → discord.ext.tasks + APScheduler   |   node-cron + BullMQ
Config         → variáveis de ambiente + Pydantic Settings / Zod
Observabilidade→ logging estruturado (structlog / pino) + Sentry
Deploy         → Docker + systemd/Compose, ou PaaS (Railway, Fly.io)
```

### 2.3. Estrutura de diretórios

**discord.py:**
```
zenibot/
├── bot.py                  # Entrypoint: subclasse de commands.Bot, setup_hook
├── config.py               # Settings via env vars (nunca literais)
├── cogs/
│   ├── moderation.py       # ban, kick, timeout, warn
│   ├── automod.py          # regras nativas + filtros customizados
│   ├── logging.py          # audit log listeners
│   ├── welcome.py          # on_member_join, autorole, verificação
│   ├── scheduler.py        # lembretes e tarefas periódicas
│   └── events.py           # Guild Scheduled Events, RSVP por botão
├── core/
│   ├── db.py               # pool de conexões
│   ├── checks.py           # decoradores de permissão reutilizáveis
│   └── embeds.py           # factories de embed padronizadas
├── migrations/
└── .env                    # NUNCA versionado
```

**discord.js:**
```
src/
├── index.ts                # Client, intents, login
├── handlers/               # loaders de comandos e eventos
├── commands/<categoria>/   # um arquivo por comando: { data, execute }
├── events/                 # um arquivo por evento: { name, once, execute }
├── components/             # handlers de botões/selects por prefixo de customId
├── jobs/                   # cron jobs
└── lib/                    # db, logger, permissions
deploy-commands.ts          # registro de slash commands (script separado)
```

---

## 3. Arquitetura para Gerenciamento de Servidores

### 3.1. Moderação automatizada

Há **duas camadas** de moderação, e a decisão de qual usar é a primeira escolha arquitetural do módulo.

#### Camada 1 — AutoMod nativo (recomendado como base)

O Discord executa as regras **antes** da mensagem ser postada, do lado do servidor. Isso significa: latência zero, zero custo de rate limit, e — crucialmente — **não exige o intent privilegiado `MESSAGE_CONTENT`**.

Objeto de regra (`POST /guilds/{guild.id}/auto-moderation/rules`) — requer `MANAGE_GUILD`:

| Trigger Type | Valor | Limite por guild | Uso |
|---|---|---|---|
| `KEYWORD` | 1 | 6 | Palavras/regex customizados |
| `SPAM` | 3 | 1 | Detecção genérica de spam |
| `KEYWORD_PRESET` | 4 | 1 | Wordsets do Discord (profanity, sexual content, slurs) |
| `MENTION_SPAM` | 5 | 1 | Limite de menções únicas + proteção anti-raid |
| `MEMBER_PROFILE` | 6 | 1 | Varredura de nome/bio do perfil |

`trigger_metadata` relevante:
- `keyword_filter`: até 1.000 frases (60 caracteres cada)
- `regex_patterns`: até 10 padrões, sintaxe **Rust-flavored**, 260 caracteres cada
- `allow_list`: exceções (até 100–1.000 frases)
- `mention_total_limit` e `mention_raid_protection_enabled`

Ações (`actions`):
- `BLOCK_MESSAGE` (1) — bloqueia, com explicação customizada de até 150 caracteres
- `SEND_ALERT_MESSAGE` (2) — envia o conteúdo para um canal de log
- `TIMEOUT` (3) — silencia por até 4 semanas; **exige `MODERATE_MEMBERS`**
- `BLOCK_MEMBER_INTERACTION` (4) — restringe interações de texto/voz

Use `exempt_roles` e `exempt_channels` para isentar staff e canais de teste.

Para reagir a acionamentos, escute `AUTO_MODERATION_ACTION_EXECUTION` (intent `AUTO_MODERATION_EXECUTION`, `1 << 21`) e registre no seu sistema de casos.

#### Camada 2 — Filtros customizados no bot

Necessária apenas para lógica que o AutoMod não cobre: heurísticas de raid, análise de anexos, detecção de contas novas, escalonamento progressivo de punições. **Exige `MESSAGE_CONTENT`** se depender do texto da mensagem.

**Padrão de escalonamento (sistema de "strikes"):**

```
infração → registra caso no DB (user_id, guild_id, tipo, motivo, moderador, timestamp)
         → conta infrações ativas na janela (ex.: 30 dias)
         → aplica ação conforme tabela configurável por guild:
              1ª → aviso (DM + log)
              2ª → timeout 10min
              3ª → timeout 24h
              4ª → kick
              5ª → ban
```

Cada caso deve ter **ID sequencial por guild**, permitindo `/case 42`, `/history @user` e revogação auditável. Este é o coração de um bot de moderação sério — e é a parte que nenhuma biblioteca dá pronta.

**Timeout é preferível a kick/ban** para infrações leves: é reversível, não notifica o servidor inteiro e o Discord já o implementa nativamente (`MODERATE_MEMBERS`, máximo de 28 dias).

```python
# discord.py — timeout com razão auditável
import datetime, discord
from discord import app_commands

@app_commands.command(description="Silencia um membro temporariamente")
@app_commands.describe(membro="Quem silenciar", minutos="Duração", motivo="Razão")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def timeout(interaction: discord.Interaction, membro: discord.Member,
                  minutos: int, motivo: str = "Sem motivo informado"):
    if membro.top_role >= interaction.user.top_role:
        return await interaction.response.send_message(
            "Você não pode moderar alguém de cargo igual ou superior.", ephemeral=True)

    await membro.timeout(datetime.timedelta(minutes=minutos), reason=motivo)
    await interaction.response.send_message(
        f"{membro.mention} silenciado por {minutos}min. Motivo: {motivo}", ephemeral=True)
```

```js
// discord.js — equivalente
const member = interaction.options.getMember('membro');
const motivo = interaction.options.getString('motivo') ?? 'Sem motivo informado';

if (member.roles.highest.position >= interaction.member.roles.highest.position) {
  return interaction.reply({ content: 'Hierarquia insuficiente.', flags: MessageFlags.Ephemeral });
}
await member.timeout(minutos * 60_000, motivo);
```

### 3.2. Logs de auditoria

Existem **duas fontes** de dados de auditoria, e o design correto combina as duas.

**Fonte A — Gateway (`GUILD_AUDIT_LOG_ENTRY_CREATE`)**
Entrega em tempo real cada nova entrada de audit log. Requer o intent `GUILD_MODERATION` (`1 << 2`) e a permissão `VIEW_AUDIT_LOG`. É a forma **correta** e moderna de logar ações administrativas — inclusive as feitas por humanos direto no cliente do Discord.

**Fonte B — REST (`GET /guilds/{guild.id}/audit-logs`)**
Requer `VIEW_AUDIT_LOG`. Parâmetros: `user_id`, `action_type`, `before`/`after` (paginação por ID de entrada), `limit` (1–100, padrão 50). Note a assimetria de ordenação: `before` retorna do mais novo para o mais antigo; `after`, o inverso. Use para *backfill* e para correlacionar um evento do Gateway (ex.: `GUILD_MEMBER_REMOVE`) com sua causa (kick vs. saída voluntária).

**Anti-padrão comum:** fazer *polling* do endpoint REST a cada poucos segundos. Isso consome rate limit inutilmente. Escute o evento do Gateway; use REST apenas para correlação pontual e histórico.

**Escrita:** toda ação do seu bot deve carregar o header `X-Audit-Log-Reason` (1–512 caracteres UTF-8 URL-encoded). Nas bibliotecas isso é o parâmetro `reason=` — ele torna as ações do bot rastreáveis no audit log nativo do Discord, independentemente do seu banco de dados.

**Arquitetura recomendada do módulo de logs:**

```
Evento do Gateway
   ↓
Normalizador (evento → { ator, alvo, ação, mudanças, timestamp, guild })
   ↓
┌─────────────────────┬──────────────────────────┐
│ Persistência (DB)   │ Formatação em Embed      │
│ — fonte da verdade  │ — apresentação humana    │
└─────────────────────┴──────────────────────────┘
   ↓                             ↓
Consultas /history       Canal de log (via Webhook)
```

Enviar logs por **Webhook** em vez de `channel.send()` é uma otimização relevante: webhooks têm bucket de rate limit próprio, isolado do bucket do bot, e permitem `username`/`avatar_url` distintos por categoria de log. Em servidores movimentados, isso evita que o fluxo de logs estrangule as respostas a comandos.

Considere ainda um **buffer com flush periódico** (agrupar N entradas ou 5 segundos, o que vier primeiro) em vez de uma requisição por evento.

### 3.3. Controle de permissões

O Discord resolve permissões em uma ordem **estritamente definida** — implementar checagens sem entendê-la produz bugs de segurança sutis.

**Permissões base:**
1. Começa com as permissões do cargo `@everyone`
2. Aplica OR com as permissões de todos os cargos do membro
3. Se `ADMINISTRATOR` estiver presente → retorna **todas** as permissões (curto-circuito)

**Overwrites de canal (aplicados nesta sequência):**
1. `deny` de `@everyone`
2. `allow` de `@everyone`
3. `deny` de cargos (união de todos os cargos do membro)
4. `allow` de cargos (união)
5. `deny` específico do membro
6. `allow` específico do membro

`ADMINISTRATOR` sobrepõe qualquer overwrite. Note que **overwrites não obedecem a hierarquia de cargos**: se dois cargos conflitam num mesmo canal, o `allow` vence.

**Hierarquia de cargos** — restrições do bot:
- Só concede/edita cargos **abaixo** do seu cargo mais alto
- Só concede permissões que ele mesmo possui
- Só modera membros cujo cargo mais alto está **abaixo** do dele

**Três camadas de autorização para comandos** (use todas):

| Camada | Onde | Função |
|---|---|---|
| `default_member_permissions` | Registro do comando | O Discord esconde o comando de quem não tem a permissão. É UX, não segurança. |
| Checagem no handler | Código do bot | Segurança real: revalide `interaction.user` e `interaction.guild.me` |
| Configuração por guild | Seu banco de dados | Cargos de staff customizados, canais permitidos, flags de módulo |

Nunca confie apenas na camada 1: administradores do servidor podem sobrescrever permissões de comandos pela UI do Discord, e a checagem no código é a única garantia.

```python
# discord.py — check reutilizável combinando permissão nativa e config por guild
from discord import app_commands

def is_staff():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild:
            return True
        staff_roles = await db.get_staff_roles(interaction.guild_id)
        return any(r.id in staff_roles for r in interaction.user.roles)
    return app_commands.check(predicate)
```

**Permissões mínimas na instalação:** monte a URL de convite com o parâmetro `permissions` calculado — não peça `ADMINISTRATOR`. Um bot de gerenciamento típico precisa de: `VIEW_CHANNEL`, `SEND_MESSAGES`, `EMBED_LINKS`, `MANAGE_MESSAGES`, `MODERATE_MEMBERS`, `KICK_MEMBERS`, `BAN_MEMBERS`, `MANAGE_ROLES`, `VIEW_AUDIT_LOG`, `MANAGE_WEBHOOKS`. Escopos: `bot` + `applications.commands` (este último é incluído automaticamente com `bot`).

Atenção: `MANAGE_ROLES` e outras permissões administrativas **exigem 2FA** quando o servidor tem 2FA obrigatório ativado — o dono do bot precisa ter 2FA na própria conta.

### 3.4. Sistema de boas-vindas

Depende do intent privilegiado **`GUILD_MEMBERS` (`1 << 1`)** — sem ele, `GUILD_MEMBER_ADD` simplesmente não chega.

**Componentes de um sistema completo:**

1. **Mensagem de boas-vindas** — canal configurável, embed com placeholders (`{user}`, `{server}`, `{count}`). Envie por webhook para isolar rate limit.
2. **Autorole** — atribuição de cargo na entrada. Exige `MANAGE_ROLES` e que o cargo alvo esteja **abaixo** do cargo do bot.
3. **Verificação / gate** — cargo de acesso concedido só após ação explícita (botão persistente, captcha, aceite de regras). Alternativa nativa: **Membership Screening** do próprio Discord — nesse caso escute `GUILD_MEMBER_UPDATE` e observe a transição de `pending: true → false`.
4. **Anti-raid** — se N entradas ocorrerem em M segundos, ative modo de bloqueio: pause o autorole, exija verificação, alerte a staff. Combine com `MENTION_SPAM` + `mention_raid_protection_enabled` do AutoMod nativo.
5. **Filtro de conta nova** — a data de criação da conta é derivável do próprio snowflake do ID, sem custo de API. Contas com menos de X dias podem ser sinalizadas.

```python
# discord.py — welcome + autorole + gate de conta nova
import datetime

class Welcome(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = await db.get_guild_config(member.guild.id)
        if not cfg.welcome_enabled:
            return

        idade = discord.utils.utcnow() - member.created_at
        if idade < datetime.timedelta(days=cfg.min_account_age_days):
            await self.bot.get_channel(cfg.alert_channel_id).send(
                f"⚠️ Conta nova: {member.mention} (criada há {idade.days}d)")
            return  # não concede autorole

        if cfg.autorole_id:
            cargo = member.guild.get_role(cfg.autorole_id)
            if cargo and cargo < member.guild.me.top_role:
                await member.add_roles(cargo, reason="Autorole de entrada")

        webhook = discord.Webhook.from_url(cfg.welcome_webhook, session=self.bot.session)
        await webhook.send(
            embed=welcome_embed(member),
            allowed_mentions=discord.AllowedMentions(users=[member]))
```

**Ponto crítico de DM:** mensagens diretas de boas-vindas falham silenciosamente se o usuário tiver DMs fechadas. Sempre envolva em `try/except discord.Forbidden` — e nunca dependa da DM para entregar informação essencial.

---

## 4. Implementação de Sistemas de Eventos e Agendamentos

### 4.1. Agendamento: escolhendo o mecanismo certo

Três mecanismos, três casos de uso distintos. Confundi-los é o erro mais comum nesta área.

| Mecanismo | Para quê | Sobrevive a restart? |
|---|---|---|
| **Loop em memória** (`@tasks.loop` / `setInterval`) | Tarefas **periódicas e sem estado**: limpar cache, atualizar presence, verificar expirações | Sim (recomeça do zero) |
| **Fila persistente** (DB + poller, BullMQ, APScheduler com jobstore) | **Agendamentos únicos** com data futura: lembretes, unban temporário, mensagens programadas | Sim, se persistido |
| **Cron externo** (systemd timer, cron do host) | Rotinas de infraestrutura: backup, relatórios diários | Sim |

**Regra de ouro:** `setTimeout` / `asyncio.sleep` para um lembrete de amanhã é um bug. Um redeploy perde o agendamento. **Persista no banco, faça poll.**

#### Padrão recomendado: tabela + poller

```sql
CREATE TABLE agendamentos (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    channel_id   BIGINT NOT NULL,
    user_id      BIGINT NOT NULL,
    tipo         TEXT   NOT NULL,        -- 'lembrete' | 'unban' | 'unmute' | 'mensagem'
    payload      JSONB  NOT NULL,
    executar_em  TIMESTAMPTZ NOT NULL,
    executado_em TIMESTAMPTZ,
    tentativas   INT DEFAULT 0
);
CREATE INDEX idx_pendentes ON agendamentos (executar_em)
    WHERE executado_em IS NULL;
```

```python
# discord.py — poller com discord.ext.tasks
from discord.ext import tasks, commands

class Scheduler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.processar.start()

    def cog_unload(self):
        self.processar.cancel()

    @tasks.loop(seconds=30)
    async def processar(self):
        # SELECT ... FOR UPDATE SKIP LOCKED torna o poller seguro com múltiplos shards
        jobs = await db.claim_due_jobs(limit=50)
        for job in jobs:
            try:
                await self.executar(job)
                await db.mark_done(job.id)
            except Exception:
                log.exception("Falha no job %s", job.id)
                await db.reschedule_with_backoff(job.id)

    @processar.before_loop
    async def antes(self):
        await self.bot.wait_until_ready()   # obrigatório: cache precisa estar pronto
```

O hook `before_loop` com `wait_until_ready()` **não é opcional** — sem ele o loop dispara antes do cache de guilds existir e `get_channel()` retorna `None`.

Para precisão de segundos com muitos jobs, use uma abordagem híbrida: o poller carrega apenas os jobs dos próximos 60 segundos em memória e os agenda com precisão; jobs mais distantes ficam só no banco.

```js
// discord.js — equivalente com node-cron para rotinas periódicas
import cron from 'node-cron';

cron.schedule('*/30 * * * * *', async () => {
  const jobs = await db.claimDueJobs(50);
  await Promise.allSettled(jobs.map(j => executar(j).then(
    () => db.markDone(j.id),
    (e) => { logger.error({ e, job: j.id }); return db.rescheduleWithBackoff(j.id); }
  )));
});
```

Sempre normalize tempo em **UTC** internamente e converta para o fuso configurado da guild apenas na apresentação. Para exibir horários, use os **timestamps dinâmicos do Discord** — `<t:1767225600:F>` renderiza no fuso local de cada leitor automaticamente, eliminando toda a classe de bugs de timezone na UI.

### 4.2. Guild Scheduled Events (eventos nativos)

Recurso nativo do Discord — aparece na aba "Eventos" do servidor, com RSVP e notificações próprias. Requer intent `GUILD_SCHEDULED_EVENTS` (`1 << 16`) para receber os eventos de Gateway.

**Entity types:**
- `STAGE_INSTANCE` (1) — evento em canal de palco
- `VOICE` (2) — evento em canal de voz
- `EXTERNAL` (3) — evento fora do Discord; exige `entity_metadata.location` e `scheduled_end_time`

**Ciclo de status** (transições irreversíveis):
```
SCHEDULED → ACTIVE → COMPLETED
SCHEDULED → CANCELED
```

Permissão base: `CREATE_EVENTS`. Eventos em canal exigem também acesso ao canal alvo.

**Recorrência:** o objeto suporta `recurrence_rule` no padrão iCalendar (RFC), com `frequency` (`YEARLY`, `MONTHLY`, `WEEKLY`, `DAILY`) e modificadores `by_weekday`, `by_n_weekday`, `by_month`. Há combinações mutuamente exclusivas e campos que o cliente não pode definir — valide contra a documentação antes de construir a regra.

**Padrão de integração:** deixe o Discord ser a fonte da verdade do evento e **espelhe** no seu banco. Escute `GUILD_SCHEDULED_EVENT_CREATE/UPDATE/DELETE` e `..._USER_ADD/REMOVE` para: enviar lembrete próprio 1h antes (via seu scheduler), criar thread de discussão automaticamente, e pingar os interessados quando o status virar `ACTIVE`.

### 4.3. Webhooks e integração com APIs externas

#### Saída: bot → serviço externo

Padrão para logs, notificações e mensagens de alta frequência. `POST /webhooks/{webhook.id}/{webhook.token}` — parâmetros úteis: `username`, `avatar_url`, `thread_id` (postar dentro de thread), `wait` (aguardar confirmação e receber o objeto da mensagem; por padrão `false`).

Requer `MANAGE_WEBHOOKS` para criar. Nomes: 1–80 caracteres, não podem conter "clyde" nem "discord".

**Segurança:** a URL do webhook **é a credencial**. Quem a possui pode postar no canal. Trate como secret: variável de ambiente, nunca em log, nunca em repositório. Se vazar, delete e recrie o webhook.

Sempre passe `allowed_mentions` explícito e sanitize conteúdo de origem externa antes de embutir na mensagem.

#### Entrada: serviço externo → bot

O Discord não entrega HTTP externo ao seu bot. Você precisa de um **servidor HTTP próprio** rodando no mesmo processo (`aiohttp.web` no discord.py, `fastify`/`express` no discord.js) ou de uma fila (Redis/RabbitMQ) entre o receptor e o bot.

```python
# discord.py — endpoint HTTP no mesmo processo, compartilhando o event loop
from aiohttp import web
import hmac, hashlib

async def handler(request: web.Request):
    corpo = await request.read()
    assinatura = request.headers.get('X-Signature', '')
    esperado = hmac.new(SEGREDO, corpo, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(assinatura, esperado):
        return web.Response(status=401)

    dados = await request.json()
    canal = bot.get_channel(dados['channel_id'])
    await canal.send(embed=montar_embed(dados))
    return web.Response(status=204)
```

**Sempre valide assinatura HMAC** em webhooks de entrada. Um endpoint aberto que posta no Discord é um canal de spam para qualquer um que descubra a URL.

Para consumo de APIs externas (chamadas *outbound*), aplique: timeout explícito, retry com backoff exponencial, circuit breaker, e cache. Uma API externa lenta que trava um handler de interação queima o prazo de 3 segundos — **defira antes de chamar**.

### 4.4. Interações por botões e componentes

Botões e select menus substituíram reações como mecanismo primário de interação — são mais rápidos, não exigem permissões de reação, e não precisam do intent `GUILD_MESSAGE_REACTIONS`.

**Limites (Components V2, ativado pela flag `IS_COMPONENTS_V2` = `1 << 15`):**
- Até 40 componentes por mensagem
- Cada Action Row: até 5 botões **ou** 1 select menu
- `custom_id`: 1–100 caracteres
- Select menus: até 25 opções, com `min_values`/`max_values`
- Estilos de botão: Primary, Secondary, Success, Danger, Link (exige `url`), Premium (exige `sku_id`)
- Com a flag V2 ativa, `content` e `embeds` ficam **desabilitados** — e a flag não pode ser removida da mensagem depois

Tipos de select: String, User, Role, Mentionable, Channel. Layout: Section, Container, Label, Separator. Conteúdo: Text Display, Thumbnail, Media Gallery, File.

#### O padrão crítico: componentes persistentes

Um *collector* vive na memória do processo. Após um restart, botões de painéis permanentes (ticket, role picker, verificação) ficam mortos. A solução é **codificar o estado no `custom_id`** e registrar um handler global:

```
custom_id = "role:toggle:1234567890"
            └─┬─┘ └──┬──┘ └────┬─────┘
           módulo  ação    parâmetro
```

```python
# discord.py — View persistente (timeout=None + custom_id fixo)
class RolePicker(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)   # obrigatório para persistência

    @discord.ui.button(label="Notificações", custom_id="role:toggle:notif",
                       style=discord.ButtonStyle.secondary)
    async def notif(self, interaction: discord.Interaction, _: discord.ui.Button):
        cargo = interaction.guild.get_role(ID_NOTIF)
        if cargo in interaction.user.roles:
            await interaction.user.remove_roles(cargo, reason="Self-role")
            msg = "Cargo removido."
        else:
            await interaction.user.add_roles(cargo, reason="Self-role")
            msg = "Cargo adicionado."
        await interaction.response.send_message(msg, ephemeral=True)

# em setup_hook — re-registra a view após todo restart:
async def setup_hook(self):
    self.add_view(RolePicker())
```

```js
// discord.js — roteador global de componentes por prefixo de customId
client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isButton()) return;
  const [modulo, acao, param] = interaction.customId.split(':');
  const handler = componentHandlers.get(`${modulo}:${acao}`);
  if (!handler) return;
  try {
    await handler(interaction, param);
  } catch (err) {
    logger.error(err);
    const resposta = { content: 'Erro ao processar.', flags: MessageFlags.Ephemeral };
    interaction.replied || interaction.deferred
      ? await interaction.followUp(resposta)
      : await interaction.reply(resposta);
  }
});
```

**Quando ainda usar reações:** enquetes rápidas descartáveis e compatibilidade legada. Reaction roles são hoje um anti-padrão — exigem `GUILD_MESSAGE_REACTIONS`, tratamento de eventos *raw* (mensagens antigas não estão em cache) e oferecem UX pior.

**Modais** (`MODAL_SUBMIT`, tipo 5) são a escolha certa para entrada de texto longo: motivo de report, formulário de aplicação, configuração multi-campo. Não podem ser abertos como resposta a um defer — precisam ser a resposta imediata à interação.

---

## 5. Segurança, Intents & Hospedagem

### 5.1. Manuseio de token

O token do bot é equivalente a uma senha com acesso total à aplicação. A documentação oficial é enfática sobre ser *"secure-make-sure-your-info-is-not-in-your-source-code careful"*.

**Regras não-negociáveis:**

1. **Nunca no código-fonte.** Use variáveis de ambiente ou um secret manager.
2. **`.env` sempre no `.gitignore`**, com um `.env.example` versionado contendo apenas as chaves.
3. **Escaneie o histórico do Git**, não só o working tree. Se o token já foi commitado, ele está comprometido — `git rm` não basta. Regenere no Developer Portal.
4. **Reset imediato ao suspeitar de vazamento.** O Discord detecta tokens em repositórios públicos do GitHub e reseta automaticamente — mas não conte com isso.
5. **Nunca logue o token**, nem em mensagens de erro. Filtre-o do output do Sentry/logger.
6. **Tokens diferentes por ambiente** (dev/staging/prod) — use aplicações separadas no portal.
7. **`client_secret` é uma credencial separada** e igualmente sensível; só é necessário se você tiver OAuth2 (dashboard web).

Nota importante: o Discord **reseta seu token automaticamente** se você exceder 1.000 `IDENTIFY` em 24 horas. Um crash-loop em produção pode, portanto, derrubar seu bot por vias que não parecem relacionadas a segurança.

### 5.2. Intents: princípio do menor privilégio

Três intents são **privilegiados** e vêm desabilitados por padrão:

| Intent | Bit | Concede | Necessário para |
|---|---|---|---|
| `GUILD_MEMBERS` | `1 << 1` | `GUILD_MEMBER_ADD/UPDATE/REMOVE`, lista completa de membros | Boas-vindas, autorole, logs de entrada/saída |
| `GUILD_PRESENCES` | `1 << 8` | Status/atividade dos membros | Raramente necessário — evite |
| `MESSAGE_CONTENT` | `1 << 15` | Campos `content`, `embeds`, `attachments`, `components` de mensagens de terceiros | Filtros customizados, comandos por prefixo |

**Regras de acesso:** abaixo de 10.000 usuários, você habilita direto no Developer Portal (aba *Bot* → *Privileged Gateway Intents*). Ao ultrapassar 10.000, é **obrigatória a verificação** com justificativa por intent — e a aprovação precisa ser **reconfirmada anualmente**.

**`MESSAGE_CONTENT` merece atenção especial.** Ele é uma exceção arquitetural: não desbloqueia eventos, mas preenche campos. Sem ele, `message.content` chega vazio — exceto em mensagens do próprio bot, DMs com o bot, mensagens que o mencionam, e context menu commands.

**Alternativas que evitam esse intent** (e devem ser preferidas):
- Slash commands em vez de comandos por prefixo
- **AutoMod nativo** em vez de filtro de conteúdo próprio
- Context menu commands para agir sobre mensagens específicas
- Mensagens que mencionam o bot (o conteúdo vem preenchido)

Um bot de gerenciamento bem projetado hoje frequentemente precisa **apenas** de `GUILD_MEMBERS`.

Intents não-privilegiados úteis para este escopo: `GUILDS` (`1 << 0`, essencial para o cache), `GUILD_MODERATION` (`1 << 2`, bans + audit log entries), `AUTO_MODERATION_CONFIGURATION` (`1 << 20`), `AUTO_MODERATION_EXECUTION` (`1 << 21`), `GUILD_SCHEDULED_EVENTS` (`1 << 16`), `GUILD_MESSAGES` (`1 << 9`, metadados de mensagem sem o conteúdo).

```python
intents = discord.Intents.none()
intents.guilds = True
intents.members = True                    # privilegiado
intents.moderation = True
intents.auto_moderation_execution = True
intents.guild_scheduled_events = True
# intents.message_content permanece False
```

### 5.3. Segurança de aplicação

**Validação de entrada.** Toda string vinda de usuário é hostil. Sanitize antes de: interpolar em query SQL (**use sempre queries parametrizadas**), embutir em mensagem, passar para shell, ou usar como regex (regex de usuário → ReDoS).

**Menções.** `allowed_mentions` explícito em toda saída. Sem ele, um comando `/say` vira ferramenta de mass-ping e um log de mensagem deletada re-notifica todo mundo.

**Autorização em múltiplas camadas** (ver §3.3) — nunca confie apenas em `default_member_permissions`.

**Rate limiting próprio.** Além dos limites do Discord, implemente cooldowns por usuário e por guild em comandos custosos. discord.py oferece `@app_commands.checks.cooldown()`; em discord.js, use um bucket em Redis.

**Isolamento de dados entre guilds.** Toda query deve filtrar por `guild_id`. Um bug aqui vaza configurações e histórico de moderação entre servidores — é a falha de segurança mais grave e mais comum em bots multi-guild.

**Tratamento de erro que não vaza.** Um traceback enviado ao canal pode expor caminhos de arquivo, queries e — no pior caso — secrets. Logue detalhes internamente; ao usuário, mensagem genérica com um ID de correlação.

**Dependências.** Fixe versões (`package-lock.json`, `requirements.txt` com hashes ou `uv.lock`). Rode `npm audit` / `pip-audit` no CI.

### 5.4. Hospedagem e operação

**Requisitos:** conexão estável e ininterrupta (WebSocket persistente), IPv4/IPv6 confiável, e reinício automático em caso de falha. RAM cresce com o número de guilds e o tamanho do cache — um bot pequeno roda em 256 MB; com `GUILD_MEMBERS` e milhares de guilds, planeje GBs.

**Opções:**

| Modelo | Prós | Contras |
|---|---|---|
| VPS (Hetzner, DigitalOcean, Oracle Free) | Controle total, custo previsível, melhor $/recurso | Você administra o SO |
| PaaS (Railway, Fly.io, Render) | Deploy trivial, TLS e logs prontos | Mais caro em escala; cuidado com planos que "dormem" |
| Container orquestrado (K8s, ECS) | Escala e rollout controlados | Complexidade desproporcional abaixo de milhares de guilds |
| Serverless (Workers, Lambda) | Custo quase zero, escala infinita | **Só funciona com HTTP Interactions** — sem Gateway, sem eventos |

**Nunca hospede em plano gratuito que hiberna por inatividade.** Cada "acordar" custa um `IDENTIFY`; hibernações frequentes esbarram no limite diário e disparam reset de token.

**Checklist de produção:**

- [ ] Restart automático com **backoff exponencial**, não imediato (protege o orçamento de `IDENTIFY`)
- [ ] Healthcheck que verifica latência do Gateway, não só "o processo está vivo"
- [ ] Logging estruturado (JSON) com `guild_id` e `user_id` correlacionáveis
- [ ] Sentry ou equivalente para exceções não tratadas — incluindo em tasks de background, que falham silenciosamente por padrão
- [ ] Backup automatizado e **testado** do banco de dados
- [ ] Migrações versionadas (Alembic / Prisma / Drizzle), nunca DDL manual
- [ ] Shutdown gracioso: `SIGTERM` → parar de aceitar interações → drenar jobs em voo → fechar WebSocket → sair
- [ ] Registro de slash commands em **script separado do boot** — registrar a cada inicialização desperdiça rate limit; use guild-scoped em dev (propagação instantânea) e global em produção
- [ ] Métricas: latência do Gateway, `429` recebidos, duração de comandos, tamanho da fila de jobs
- [ ] Sharding planejado antes de 2.500 guilds — a migração sob pressão é dolorosa

---

## 6. Referências & Documentações Oficiais

### Documentação oficial do Discord

- [Discord Developer Documentation](https://docs.discord.com/developers/) — portal principal
- [Gateway](https://docs.discord.com/developers/topics/gateway) — intents, conexão, resume, sharding, rate limits do Gateway
- [Rate Limits](https://docs.discord.com/developers/topics/rate-limits) — buckets, headers, 429, limite de requisições inválidas
- [Permissions](https://docs.discord.com/developers/topics/permissions) — flags, hierarquia, ordem de cálculo de overwrites
- [OAuth2](https://docs.discord.com/developers/topics/oauth2) — escopos `bot`/`applications.commands`, contextos de instalação
- [Auto Moderation](https://docs.discord.com/developers/resources/auto-moderation) — triggers, metadata, ações, limites por guild
- [Audit Log](https://docs.discord.com/developers/resources/audit-log) — endpoint, estrutura, `X-Audit-Log-Reason`
- [Guild Scheduled Event](https://docs.discord.com/developers/resources/guild-scheduled-event) — entity types, status, recorrência
- [Webhook](https://docs.discord.com/developers/resources/webhook) — tipos, execução, parâmetros
- [Receiving and Responding to Interactions](https://docs.discord.com/developers/interactions/receiving-and-responding) — prazos de 3s/15min, defer, ephemeral
- [Message Components Reference](https://docs.discord.com/developers/components/reference) — Components V2, botões, selects, modais
- [Discord Developer Portal](https://discord.com/developers/applications) — gerenciamento de aplicações e intents

### Suporte a desenvolvedores (políticas de intents)

- [What are Privileged Intents?](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents)
- [How do I get Privileged Intents for my bot?](https://support-dev.discord.com/hc/en-us/articles/6205754771351-How-do-I-get-Privileged-Intents-for-my-bot)
- [Getting Started with Privileged Intent Review](https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review)
- [Message Content Intent Alternatives / Workarounds](https://support-dev.discord.com/hc/en-us/articles/6383579033751-Message-Content-Intent-Alternatives-Workarounds)
- [Changes to Privileged Intent Access for Discord Apps](https://support-dev.discord.com/hc/en-us/articles/40281523410967-Changes-to-Privileged-Intent-Access-for-Discord-Apps)

### discord.js — v14.27.0 (Node.js ≥ 18)

- [discord.js Guide](https://discordjs.guide/) — guia oficial
- [Sharding](https://discordjs.guide/sharding/) e [Additional Information](https://discordjs.guide/sharding/additional-information)
- [Repositório oficial](https://github.com/discordjs/discord.js/) · [Releases](https://github.com/discordjs/discord.js/releases) · [npm](https://www.npmjs.com/package/discord.js)
- [@discordjs/rest](https://www.npmjs.com/package/@discordjs/rest) — cliente REST com rate limiting
- [@discordjs/core](https://www.npmjs.com/package/@discordjs/core) — camada de baixo nível
- [@discordjs/voice](https://www.npmjs.com/package/@discordjs/voice) — áudio

### discord.py — v2.7.1 (Python ≥ 3.8)

- [Documentação oficial](https://discordpy.readthedocs.io/en/stable/) · [PyPI](https://pypi.org/project/discord.py/)
- [`discord.ext.tasks`](https://discordpy.readthedocs.io/en/stable/ext/tasks/index.html) — loops de background
- [`discord.ext.commands` API Reference](https://discordpy.readthedocs.io/en/stable/ext/commands/api.html) — Bot, Cog, Context
- [Cogs](https://discordpy.readthedocs.io/en/latest/ext/commands/cogs.html) — organização modular
- [Changelog](https://discordpy.readthedocs.io/en/stable/whats_new.html)
- [discord.py Masterclass](https://fallendeity.github.io/discord.py-masterclass/) — material comunitário de alta qualidade

### Outras bibliotecas

- [JDA (Java)](https://github.com/discord-jda/JDA) — alternativa JVM, tipagem forte e alta performance
- [Pycord](https://docs.pycord.dev/) — fork do discord.py com ciclo de release próprio
