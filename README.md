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
| `cogs/selfroles.py` | `/painel criar`, `/painel adicionar`, `/painel remover` — botões persistentes de auto-atribuição de cargos |
| `cogs/health.py` | Backup automático do banco, `/backup`, aviso de inicialização |
| `cogs/escalation.py` | `/escalonamento ver`, `definir`, `remover` — punições automáticas por número de infrações |
| `cogs/antiraid.py` | `/antiraid ver`, `configurar`, `liberar` — detecção de picos de entrada |
| `cogs/builder.py` | `/embed criar` — painel interativo de criação de embeds |

**Ainda não implementado** (ver "Próximos passos"): gestão de regras do AutoMod via comando, Guild Scheduled Events, anti-raid.

### Painéis de self-roles

```
/painel criar canal:#cargos titulo:Escolha seus cargos
/painel adicionar mensagem:<id> cargo:@Notificações emoji:🔔
/painel remover mensagem:<id> cargo:@Notificações
```

O `mensagem` aceita o ID ou o link completo da mensagem (botão direito >
Copiar link da mensagem). Até 20 cargos por painel.

### Escalonamento automático

Monte a régua e o `/aviso` passa a aplicar a punição sozinho:

```
/escalonamento definir limite:3 acao:Timeout duracao:1h
/escalonamento definir limite:5 acao:Banir duracao:7d
/escalonamento ver
```

Conta infrações **ativas dos últimos 30 dias**. Se a hierarquia impedir a
punição, o moderador é avisado na resposta do `/aviso` e nada fica registrado
como se tivesse sido aplicado.

### Construtor de embeds

```
/embed criar
```

Abre um painel efêmero com prévia ao vivo: botões para **Conteúdo**,
**Aparência** e **Rodapé**, um seletor de canal de destino, e **Publicar**.
Os formulários já vêm preenchidos com o que você digitou antes, então ajustar
não exige redigitar tudo.

O contador de caracteres no topo avisa antes de estourar o limite de 6000 do
Discord — que soma título, descrição, rodapé e autor, não só o texto.

> O rascunho vive **apenas em memória**, com timeout de 15 minutos. É o oposto
> dos painéis de self-role: um rascunho que sobrevivesse a um deploy seria
> lixo acumulado, não funcionalidade.

### Anti-raid

```
/antiraid configurar entradas:5 janela:30s acao:Somente alertar
/antiraid ver
```

Detecta picos por janela deslizante de entradas. O alerta menciona os cargos
de staff, mostra quantas contas são recém-criadas e lista os primeiros que
entraram.

A ação `Bloquear servidor e alertar` pausa convites e eleva a verificação por
um tempo, restaurando o estado anterior depois. **Exige a permissão Gerenciar
Servidor**, que não está no convite padrão — o bot avisa se ela faltar, e o
alerta continua funcionando. Para concedê-la, reconvide com:

```
https://discord.com/oauth2/authorize?client_id=1544424196686356480&permissions=1100317027494&scope=bot+applications.commands
```

`/antiraid liberar` remove o bloqueio antes da hora.

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

Para inspecionar o que está registrado, e em qual escopo:

```bash
python scripts/list_commands.py
```

> **Comandos duplicados no Discord?** Quase sempre é o mesmo comando
> registrado nos dois escopos — global **e** por servidor. O cliente mostra
> os dois, sem erro nenhum. Confirme com `list_commands.py` e remova um dos
> lados: `sync_commands.py --global --clear` apaga os globais (recomendado
> para poucos servidores, já que o escopo de guild propaga na hora), ou
> `sync_commands.py --clear` apaga os da guild.

Em produção com muitos servidores, registre globalmente (propagação de até 1h)
— e **não** sincronize por guild, ou os comandos duplicam:

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

## Backup e monitoramento

O banco é copiado automaticamente a cada `ZENIBOT_BACKUP_INTERVAL_HOURS`
(padrão 24, `0` desativa), guardando os `ZENIBOT_BACKUP_KEEP` mais recentes
(padrão 7). Os arquivos ficam em `data/backups/`, **ao lado do banco** — o que
no Docker significa dentro do volume `zenibot-data`, e não perdidos no
sistema de arquivos efêmero do container.

Backup sob demanda (restrito aos donos):

```
/backup
```

Restaurar é copiar o arquivo por cima do banco com o bot parado:

```bash
docker compose down
```

```bash
docker run --rm -v zenibot-data:/data alpine sh -c "cp /data/backups/zenibot-AAAAMMDD-HHMMSS.db /data/zenibot.db"
```

Ao subir, `ZENIBOT_STARTUP_NOTICE=true` (padrão) publica um aviso no canal de
alertas — ou no de logs, se não houver — com versão, latência, tamanho do
banco e política de backup. É o que faz uma queda silenciosa virar uma queda
percebida: se o aviso parou de aparecer, algo está errado.

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

**Botões persistentes sem estado em memória.** Uma `View` comum morre no
restart. Em vez de guardar cada painel no banco e re-registrar uma view por
painel no boot, os self-roles usam `discord.ui.DynamicItem`: o botão se
reconstrói a partir do próprio `custom_id` da mensagem (`srole:<role_id>`),
casado por regex. Um único `add_dynamic_items()` faz qualquer painel, de
qualquer época, voltar a responder — sem tabela e sem consulta na partida.

**Self-roles nunca entregam poder.** Cargos com permissão administrativa
(`administrator`, `manage_roles`, `ban_members` e mais 11) são recusados no
painel: um botão público que concede esses cargos é escalada de privilégio
para qualquer membro. A checagem roda ao montar o painel **e de novo a cada
clique**, porque as permissões de um cargo podem mudar depois. Quem cria o
painel também não pode expor cargo igual ou acima do seu próprio.

**Anti-raid detecta em memória, mas desfaz com estado persistido.** A janela
deslizante de entradas vive só na RAM — um raid é, por definição, algo
acontecendo agora, e persistir isso só criaria o risco de reagir a um pico já
passado. O **bloqueio**, porém, é o oposto: o estado anterior do servidor
(nível de verificação, convites pausados) vai no payload de um job na fila
persistente. Sem isso, um restart durante o bloqueio deixaria o servidor
trancado para sempre.

**Escalonamento dispara no limiar exato, e não se realimenta.** Duas decisões
que evitam comportamento surpreendente: a regra da 3ª infração dispara na 3ª,
não em toda infração a partir dela — com `>=`, o servidor aplicaria a mesma
pena indefinidamente, que é o oposto de escalonar. E a punição gerada é
gravada com `automatic = 1`, ficando fora da contagem: contá-la faria a
punição da 3ª virar a 4ª infração, e a advertência seguinte cairia numa regra
mais dura do que a merecida.

**Backup usa a API online do SQLite, não cópia de arquivo.** Com WAL ativo,
parte dos dados vive no `-wal`: um `cp` do `.db` com o bot rodando produz um
snapshot incompleto que *parece* válido. `Connection.backup()` lê as páginas
sob lock, com o banco em uso. A escrita vai para um `.partial` renomeado no
fim, então um backup interrompido nunca deixa um `.db` truncado. O loop
também **não** espera o Gateway: se a API do Discord estiver fora, é
justamente quando não se quer perder o backup do dia.

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

- [ ] **Logs via webhook** em vez de `channel.send()` — bucket de rate limit
      próprio, isolado do bucket do bot
- [ ] **Gestão de regras do AutoMod** por slash command
- [ ] **Guild Scheduled Events** — espelhar em banco e lembrar os interessados
- [ ] **Testes de integração** com `dpytest` (a suíte atual cobre lógica e
      persistência, mas não o ciclo completo de uma interação)

---

## Desenvolvimento

### Testes

85 testes, nenhum toca a rede ou o Discord — banco temporário por teste e
credenciais falsas. Instale as dependências de desenvolvimento e rode:

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

> Rode com o interpretador do venv. Se houver outro Python no PATH, um
> `pytest` solto usa as dependências **dele** e falha na importação do
> `discord`. Com o venv ativado, `pytest` já resolve certo; sem ativar, use
> `& .\.venv\Scripts\python.exe -m pytest`.

| Arquivo | Cobre |
|---|---|
| `tests/test_core.py` | Duração, redação de token no log, regras de escalonamento, janela anti-raid, segurança dos self-roles |
| `tests/test_db.py` | Config por guild, casos, regras, fila de jobs, backup, migrações sobre banco povoado |
| `tests/test_bot.py` | Configuração, intents, carga de cogs, árvore de comandos, backup e retenção |
| `tests/test_supervise.py` | Reconexão com backoff e os erros que **não** devem ser repetidos |

Para validar uma **imagem Docker** — onde a suíte não existe, por ser
dependência de desenvolvimento — use a verificação de deploy:

```bash
docker run --rm zenibot:latest python scripts/healthcheck.py
```

Recarregar um cog sem reiniciar o bot: `/reload cog:moderation`

Lint:

```bash
ruff check .
```

### Nunca commite o `.env`

O `.gitignore` já cobre. Se um token vazar para o histórico do Git, `git rm` não
resolve — **resete o token** no Developer Portal imediatamente.
