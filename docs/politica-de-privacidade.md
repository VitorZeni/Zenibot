# Política de Privacidade — Zenibot

**Última atualização:** 01/09/2026
**Responsável pelo tratamento dos dados:** Zenibot
**Contato:** zenola.zenibot@gmail.com

Esta política descreve quais dados o bot **Zenibot** ("o bot") coleta, por que
os coleta e o que você pode fazer a respeito. Ela se aplica exclusivamente ao
Zenibot e não à plataforma Discord, cujo tratamento de dados é regido pela
[Política de Privacidade do Discord](https://discord.com/privacy).

---

## 1. Resumo

O Zenibot é uma ferramenta de moderação e agendamento. Ele armazena o mínimo
necessário para funcionar: identificadores numéricos, configurações do servidor
e o histórico de ações de moderação.

**O Zenibot não lê nem armazena o conteúdo das suas mensagens.** O bot opera
sem o *intent* privilegiado `MESSAGE_CONTENT` do Discord, o que significa que
a plataforma não lhe entrega o texto das mensagens dos canais. Essa é uma
restrição técnica imposta pela própria API, não apenas uma promessa.

---

## 2. Dados coletados e armazenados

### 2.1 Configuração do servidor

Armazenados quando um administrador configura o bot via `/config`:

| Dado | Finalidade |
|---|---|
| ID do servidor (guild) | Identificar a que servidor a configuração pertence |
| IDs de canais (logs, alertas, boas-vindas) | Saber onde publicar cada tipo de mensagem |
| ID do cargo de autorole | Atribuir o cargo a novos membros |
| IDs dos cargos de staff | Determinar quem pode usar os comandos de moderação |
| Idade mínima de conta configurada | Sinalizar contas recém-criadas |

### 2.2 Histórico de moderação

Armazenado quando um moderador executa `/aviso`, `/silenciar`, `/expulsar`,
`/banir` ou `/desbanir`:

| Dado | Finalidade |
|---|---|
| ID do usuário moderado | Vincular a ação ao histórico da pessoa |
| ID do moderador | Responsabilização e auditoria interna do servidor |
| Tipo da ação e duração | Registro da penalidade aplicada |
| **Motivo (texto livre)** | Justificar a ação para a equipe do servidor |
| Data e hora | Ordenação e expiração de penalidades |

> **Atenção ao campo "motivo".** Ele é preenchido livremente pelo moderador. Se
> alguém escrever dados pessoais ali, esses dados serão armazenados. A equipe de
> moderação de cada servidor é responsável pelo que escreve nesse campo.

### 2.3 Agendamentos

Armazenado quando alguém usa `/lembrete` ou quando um banimento temporário é
aplicado:

| Dado | Finalidade |
|---|---|
| ID do usuário e do canal | Saber a quem e onde entregar |
| **Texto do lembrete** | É o conteúdo que será entregue a você |
| Data e hora de execução | Disparar no momento certo |

O texto do lembrete é fornecido voluntariamente por você ao usar o comando.

### 2.4 Eventos de auditoria

O bot recebe eventos do registro de auditoria do Discord (criação de canais,
alterações de cargo, banimentos etc.) e os **republica no canal de logs**
configurado pelo servidor. Esses eventos **não são gravados no banco de dados do
bot** — eles apenas transitam pela memória e viram uma mensagem no Discord, que
passa a ser regida pelas políticas do próprio Discord.

---

## 3. Dados que o Zenibot NÃO coleta

- Conteúdo de mensagens dos canais
- Mensagens diretas (exceto as que o próprio bot envia a você)
- Áudio, vídeo ou qualquer dado de canais de voz
- Endereço de e-mail, nome real, telefone, endereço IP ou dados de pagamento
- Anexos, imagens ou arquivos
- Qualquer dado para fins de publicidade, perfilamento ou treinamento de modelos

---

## 4. Como os dados são armazenados

Os dados ficam em um banco de dados local, sob controle do operador desta
instância do bot (Zenibot, contato acima), hospedado em servidor privado
localizado em Curitiba, Paraná, Brasil.

O acesso é restrito ao operador. Não há venda, aluguel, compartilhamento ou
transferência dos dados a terceiros — nem para fins comerciais, nem analíticos.
Não utilizamos serviços de análise, rastreamento ou publicidade.

---

## 5. Retenção

| Categoria | Prazo |
|---|---|
| Configuração do servidor | Enquanto o bot permanecer no servidor |
| Histórico de moderação | 12 meses ou até solicitação de exclusão |
| Agendamentos | Excluídos após a execução ou o cancelamento |

Ao remover o bot do servidor, você pode solicitar a exclusão de todos os dados
daquele servidor pelo contato indicado acima.

---

## 6. Seus direitos

Conforme a **Lei Geral de Proteção de Dados** (Lei nº 13.709/2018) e, para
usuários no Espaço Econômico Europeu, o **GDPR**, você pode solicitar:

- **Acesso** — uma cópia dos dados que temos sobre você
- **Correção** — retificação de dados incorretos
- **Exclusão** — remoção dos seus dados
- **Informação** — com quem os dados são compartilhados (resposta: com ninguém)
- **Oposição** — deixar de usar o bot e pedir a remoção dos seus registros

Para exercer qualquer um deles, escreva para **zenola.zenibot@gmail.com**
informando seu ID de usuário do Discord e o servidor em questão. Responderemos
em até 30 dias.

> **Limite importante:** o histórico de moderação existe para a segurança da
> comunidade. Pedidos de exclusão de registros de moderação **ativos** podem ser
> recusados quando houver interesse legítimo do servidor em mantê-los — por
> exemplo, o registro de um banimento em vigor. Nesse caso, explicaremos o motivo.

---

## 7. Segurança

O token de acesso do bot é mantido fora do código-fonte, em variável de
ambiente, e é filtrado dos registros de log. O acesso ao banco é restrito ao
operador. Ainda assim, nenhum sistema é totalmente imune: em caso de incidente de
segurança que afete seus dados, os servidores impactados serão notificados.

---

## 8. Idade mínima

O Zenibot segue os [Termos de Serviço do Discord](https://discord.com/terms),
que exigem idade mínima de 13 anos (ou a idade mínima legal do seu país). O bot
não é direcionado a crianças e não coleta dados conscientemente de menores de 13
anos.

---

## 9. Alterações

Esta política pode ser atualizada. A data de "última atualização" no topo sempre
refletirá a versão vigente. Mudanças significativas serão anunciadas no servidor
de suporte, se houver.

---

## 10. Contato

Dúvidas, solicitações ou reclamações: zenola.zenibot@gmail.com
