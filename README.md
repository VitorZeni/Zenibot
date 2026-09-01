# Zenibot

Bot de gerenciamento de servidor e eventos para Discord, em `discord.py` 2.7.

Base arquitetural documentada em [GUIA-BOTS-DISCORD.md](GUIA-BOTS-DISCORD.md).

---

## O que já está implementado

| Módulo | Comandos / Listeners |
|---|---|
| `cogs/admin.py` | `/config ver`, `/config canal`, `/config boas-vindas`, `/config autorole`, `/config idade-minima`, `/config staff`, `/ping`, `/sync`, `/reload` |
| `cogs/moderation.py` | `/aviso`, `/silenciar`, `/expulsar`, `/banir` (com duração), `/desbanir`, `/caso`, `/historico` |
| `cogs/welcome.py` | `on_member_join` → triagem de conta nova, autorole, mensagem de boas-vindas |
| `cogs/logs.py` | `on_audit_log_entry_create`, `on_automod_action` → canal de logs |
| `cogs/scheduler.py` | Poller de jobs persistentes, `/lembrete`, desbanimento automático |

**Ainda não implementado** (ganchos prontos, ver "Próximos passos"): gestão de regras do AutoMod via comando, Guild Scheduled Events, painéis com botões persistentes.

---

## Setup

### 1. Pré-requisitos

Python 3.11+ (validado em **3.14.7**).

### 2. Ambiente virtual e dependências

```bash
python -m venv .venv
```

Ative o ambiente. **No PowerShell** (note o `.\` — sem ele o PowerShell tenta
carregar `.venv` como módulo e falha com `CouldNotAutoLoadModule`):

```bash
.\.venv\Scripts\Activate.ps1
```

No `cmd.exe`, use `.venv\Scripts\activate.bat`. Se o PowerShell bloquear o
script por política de execução, pule a ativação e chame o interpretador do
venv diretamente em todos os comandos:
`& .\.venv\Scripts\python.exe <script>`

```bash
pip install -e .
```

### 3. Criar a aplicação no Discord

1. Acesse o [Developer Portal](https://discord.com/developers/applications) → **New Application**
2. Aba **Bot** → **Reset Token** → copie o token
3. Ainda na aba **Bot**, em **Privileged Gateway Intents**, ative **SERVER MEMBERS INTENT**
   (obrigatório: sem ele `on_member_join` nunca dispara e as boas-vindas ficam mudas)
4. **Não** ative Message Content Intent — o bot foi desenhado para não precisar dele

### 4. Configurar o `.env`

```bash
copy .env.example .env
```

Preencha `ZENIBOT_TOKEN`, `ZENIBOT_OWNER_IDS` (seu ID de usuário) e
`ZENIBOT_DEV_GUILD_ID` (o servidor de testes).

> Para copiar IDs, ative **Configurações do usuário → Avançado → Modo desenvolvedor** no Discord.

### 5. Convidar o bot

Aba **OAuth2 → URL Generator**: escopos `bot` + `applications.commands`, e as permissões:

`Ver Canais`, `Enviar Mensagens`, `Inserir Links`, `Gerenciar Mensagens`,
`Moderar Membros`, `Expulsar Membros`, `Banir Membros`, `Gerenciar Cargos`,
`Ver Registro de Auditoria`, `Gerenciar Webhooks`

**Não peça `Administrador`.** E depois de convidar, **mova o cargo do Zenibot para cima** na lista de cargos do servidor — ele só consegue moderar membros e atribuir cargos que estejam *abaixo* do dele.

### 6. Registrar os comandos e subir

Com o venv ativado:

```bash
python scripts/sync_commands.py
```

```bash
python -m zenibot
```

Sem ativar o venv, no PowerShell:

```bash
& .\.venv\Scripts\python.exe scripts\sync_commands.py
```

Ambos funcionam de qualquer diretório: o projeto resolve `.env`, o banco e as
migrações a partir da raiz do pacote, não do diretório atual.

Em produção, registre globalmente (propagação de até 1h):

```bash
python scripts/sync_commands.py --global
```

### 7. Configurar dentro do servidor

```
/config canal tipo:Logs de auditoria canal:#logs
/config canal tipo:Boas-vindas canal:#geral
/config boas-vindas ativado:true
/config autorole cargo:@Membro
/config staff cargo:@Moderador
```

---

## Deploy com Docker

Requer o `.env` preenchido (o mesmo do setup local). O token **não** entra na
imagem: ele é injetado em tempo de execução via `env_file`.

```bash
docker compose up -d --build
```

Registrar os slash commands (uma vez, ou quando os comandos mudarem):

```bash
docker compose run --rm zenibot python scripts/sync_commands.py
```

Acompanhar os logs:

```bash
docker compose logs -f
```

Detalhes que valem saber:

- **Imagem de ~250 MB**, multi-stage: as ferramentas de build ficam no estágio
  descartado. O `pip install` só reexecuta quando o `pyproject.toml` muda.
- **Roda como `zenibot` (uid 10001)**, nunca root.
- **O banco vive no volume `zenibot-data`** montado em `/app/data`. Sem ele os
  dados morreriam a cada `docker compose up --build`. Para backup, copie
  `/app/data/zenibot.db` de dentro do container.
- **`docker stop` é gracioso**: o `python` é PID 1 (forma exec no `CMD`) e
  recebe o SIGTERM direto; o handler fecha WebSocket e banco antes de sair.
  Medido em 0,3s, bem abaixo do `stop_grace_period` de 20s.
- **`restart: unless-stopped`** complementa o supervisor interno: ele cobre
  falhas de rede dentro do processo, o Docker cobre o processo morrer de vez.
- O código **não** é instalado em `site-packages` de propósito. `config.py` e
  `db.py` resolvem `.env`, o banco e `migrations/` relativos à raiz do pacote;
  instalado no venv, essa raiz apontaria para o lugar errado e as migrações
  não seriam encontradas. O estágio de build instala as dependências e depois
  desinstala o pacote.

## Documentos legais

- [Termos de Serviço](docs/termos-de-servico.md)
- [Política de Privacidade](docs/politica-de-privacidade.md)

Ambos precisam ter os placeholders `[...]` preenchidos e ser publicados numa URL
pública antes de ir para o Developer Portal. A Discord Developer Policy exige a
política de privacidade desde a criação do app; as duas URLs viram requisito de
verificação a partir de **75 servidores**.

## Estrutura

```
zenibot/
├── __main__.py          Entrypoint: python -m zenibot
├── bot.py               Classe Zenibot, intents, error handler global
├── config.py            Settings via env vars (pydantic-settings)
├── core/
│   ├── db.py            TODO o SQL do projeto vive aqui
│   ├── checks.py        Autorização e hierarquia de cargos
│   ├── embeds.py        Factories de embed + NO_MENTIONS
│   ├── duration.py      Parsing de "1h30m"
│   └── logging_setup.py Logging + filtro de token
└── cogs/                Um módulo por domínio, recarregável a quente
migrations/              .sql aplicados em ordem, uma vez cada
scripts/sync_commands.py Registro de slash commands (fora do boot)
```

---

## Decisões de arquitetura

**Sem `MESSAGE_CONTENT`.** Todos os comandos são slash commands e a filtragem
de conteúdo é delegada ao AutoMod nativo do Discord, que roda server-side antes
da mensagem existir. Isso evita um intent privilegiado e a revisão obrigatória
acima de 10 mil usuários.

**Slash commands registrados fora do boot.** Sincronizar a cada inicialização
desperdiça rate limit. Use `scripts/sync_commands.py` ou `/sync`.

**Agendamento persistente, nunca em memória.** Um `asyncio.sleep()` para um
lembrete de amanhã é apagado pelo próximo deploy. Tudo vai para a tabela
`schedules`; um loop de 30s reivindica os jobs vencidos com `claimed_at`, com
backoff exponencial em caso de falha.

**Hierarquia validada antes da chamada REST.** Checar depois significa tomar
403 — e 10.000 respostas inválidas em 10 minutos resultam em ban temporário de
IP pela Cloudflare.

**Erros nunca vazam traceback.** Erro esperado vira mensagem clara; inesperado
vira um ID de correlação de 8 caracteres que aparece no log do servidor.

**Falha de rede não derruba o processo.** O `discord.py` já reconecta sozinho
depois de conectado, mas não cobre o `login()` inicial: sem DNS na partida, a
exceção sobe e mata o bot. O supervisor em `__main__.py` fecha essa lacuna com
backoff exponencial (5s → 300s, com jitter), reiniciando a contagem após uma
sessão estável de 60s. Erros de configuração — token inválido, intent
privilegiado faltando — são explicitamente **não** retentáveis: repetir nunca
resolveria, então o processo sai com a mensagem do que corrigir.

**Autorização em três camadas:** `default_permissions` (UX, esconde o comando)
+ checagem no handler (segurança real) + `staff_role_ids` por guild
(cargos customizados). A primeira camada sozinha não é segurança — admins podem
reconfigurar permissões de comando pela UI do Discord.

**SQLite em vez de PostgreSQL.** Desvio consciente do guia: o projeto roda sem
nenhuma infraestrutura externa. Todo o SQL está isolado em `core/db.py`.

### Migrando para PostgreSQL

Necessário quando o bot passar a rodar em múltiplos processos (sharding acima
de 2.500 guilds) ou quando a concorrência de escrita virar gargalo. O que muda,
tudo dentro de `core/db.py`:

1. `aiosqlite` → `asyncpg` com pool de conexões
2. Placeholders `?` → `$1, $2, ...`
3. `claim_due_jobs`: trocar a marcação `claimed_at` por
   `SELECT ... FOR UPDATE SKIP LOCKED`, que dá exclusão mútua real entre processos
4. Migrações: `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL`,
   timestamps ISO → `TIMESTAMPTZ`, `payload TEXT` → `JSONB`
5. `add_case`: o `BEGIN IMMEDIATE` + `MAX+1` vira uma sequence por guild ou
   um `INSERT ... RETURNING` com advisory lock

---

## Próximos passos sugeridos

- [ ] **Escalonamento automático de punições** — `db.count_active_cases()` já
      existe; falta a tabela de regras por guild (1ª → aviso, 3ª → timeout 24h…)
- [ ] **Logs via webhook** em vez de `channel.send()` — bucket de rate limit
      próprio, isolado do bucket do bot
- [ ] **Painel de self-roles com botões persistentes** — `custom_id` no formato
      `role:toggle:<id>` e `bot.add_view()` no `setup_hook` (o gancho já está lá)
- [ ] **Anti-raid** — janela deslizante de entradas em `on_member_join`
- [ ] **Gestão de regras do AutoMod** por slash command
- [ ] **Guild Scheduled Events** — espelhar em banco e lembrar os interessados
- [ ] **Testes** com `pytest` + `dpytest`

---

## Desenvolvimento

Smoke test — valida config, migrações, carga dos cogs, árvore de comandos e a
camada de banco. Não precisa de token nem de rede:

```bash
python scripts/smoke_test.py
```

Teste do supervisor de reconexão (também sem token e sem rede):

```bash
python scripts/test_reconnect.py
```

Recarregar um cog sem reiniciar o bot: `/reload cog:moderation`

Lint:

```bash
ruff check .
```

### Nunca commite o `.env`

O `.gitignore` já cobre. Se um token vazar para o histórico do Git, `git rm` não
resolve — **resete o token** no Developer Portal imediatamente.
