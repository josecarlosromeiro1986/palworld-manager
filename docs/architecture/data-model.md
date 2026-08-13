# Modelo de dados

> Status: Planejado para a V1.

O modelo usará SQLite e SQLAlchemy. As descrições abaixo são conceituais: colunas, índices e constraints serão definidos durante a implementação. **Alembic será responsável por todas as migrations do banco.** Secrets não serão armazenados nessas entidades.

## Entidades previstas

### `users`

Representa administradores, inicialmente um único usuário, com identidade e material de autenticação protegido. Relaciona-se conceitualmente a sessões, tentativas de login e eventos de auditoria.

### `sessions`

Mantém sessões server-side, seus prazos e estado de invalidação. Cada sessão pertence a um usuário; o cliente recebe apenas um identificador opaco.

### `login_attempts`

Registra informações mínimas para aplicar e auditar a proteção contra brute force. Pode ser associado a um usuário quando identificado, sem armazenar senhas ou cookies.

### `app_settings`

Armazena configurações operacionais seguras e editáveis pelo painel, como timezone, retenções e timeouts. Configurações estruturais e secrets permanecem fora dessa entidade.

### `audit_events`

Registra a trilha do que aconteceu no sistema, quem ou qual origem executou e qual foi o resultado. Pode referenciar usuário e job responsáveis, além de ações como `UPDATE_SERVER`, `BACKUP`, `RESTORE`, `BAN`, `UNBAN` e `LOGIN_BLOCKED`.

### `notification_events`

Representa eventos destinados a uma integração de notificação externa, inicialmente Discord. Qualquer componente pode criar um evento no SQLite, mas somente o worker pode realizar a entrega externa. FastAPI nunca envia diretamente ao Discord.

Guarda somente as informações necessárias para controlar e registrar a entrega: tipo do evento, canal, status, horário, job relacionado, número de tentativas e erro de entrega, quando houver. Os estados conceituais são:

```text
PENDING → SENDING → SENT
                  ↘ FAILED
```

Falhas transitórias permitem no máximo 3 tentativas totais, com pequeno backoff. Ao esgotá-las, o evento permanece `FAILED`; não há retry infinito. A estratégia exata de backoff e o schema SQL serão definidos durante a implementação.

A entrega segue semântica at least once. No startup, o worker reconcilia eventos deixados em `SENDING`: com menos de 3 tentativas, retornam a `PENDING` e uma nova tentativa será contabilizada; com 3 tentativas, passam a `FAILED`. Se a entrega externa tiver ocorrido antes da interrupção, mas `SENT` não tiver sido persistido, a nova tentativa pode produzir uma mensagem duplicada.

Exemplos conceituais incluem `BACKUP_FAILED`, `RESTORE_FAILED`, `UPDATE_COMPLETED`, `UPDATE_FAILED`, `DISK_CRITICAL`, `SERVER_CRASH`, `SERVER_RECOVERED`, `FORCED_SHUTDOWN` e `LOGIN_BLOCKED`.

Exemplo conceitual:

```text
audit_event
action: UPDATE_SERVER
actor: admin
result: FAILURE

→ notification_event
type: UPDATE_FAILED
channel: DISCORD
status: SENT
```

A entidade de notificação não duplica toda a auditoria.

### `jobs`

Representa operações persistentes em background, incluindo tipo, estado, progresso e referência ao log textual. Pode se relacionar a eventos de auditoria e registros de backup.

### `backup_records`

Cataloga backups gerenciados, sua localização, integridade e estado. Relaciona-se aos jobs que os criam, transferem ou restauram, sem guardar o conteúdo do backup no banco.

### `ban_history`

Mantém o histórico administrativo de Ban e Unban, incluindo alvo, motivo e resultado. Complementa a auditoria, mas não substitui o estado mantido pelo Palworld.

O detalhamento futuro deve preservar transações, integridade referencial e preparação para múltiplos usuários. Consulte os requisitos de banco e auditoria em [SPECIFICATION.md](../../SPECIFICATION.md).
