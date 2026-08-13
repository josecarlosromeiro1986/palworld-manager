# Modelo de dados

> Status: Em desenvolvimento. O schema inicial e a infraestrutura de migrations estão implementados; regras de domínio serão adicionadas nas etapas correspondentes.

O modelo usa SQLite e SQLAlchemy 2.x. A migration inicial cria as nove entidades previstas abaixo, com chaves estrangeiras, índices e constraints básicos. As descrições permanecem conceituais: o schema evoluirá somente por migrations conforme cada domínio for implementado. **Alembic é responsável por todas as migrations do banco; a aplicação não usa `create_all` para criar o schema.** Secrets não são armazenados nessas entidades.

Em produção, o arquivo persistente é `/var/lib/palworld-manager/manager.db`; em desenvolvimento, `app` e `worker` compartilham um volume montado nesse caminho. Conexões habilitam foreign keys, WAL e timeout de espera para concorrência local.

## Entidades previstas

### `users`

Representa administradores, inicialmente um único usuário, com identidade e hash Argon2id da senha. A criação inicial e a redefinição por CLI estão implementadas; nenhuma senha em texto puro é persistida. Relaciona-se conceitualmente a sessões, tentativas de login e eventos de auditoria.

### `sessions`

Mantém sessões server-side, seus prazos absoluto e de inatividade e o estado de revogação. Cada sessão pertence a um usuário; o cliente recebe identificadores opacos, enquanto o SQLite guarda somente hashes dos tokens de sessão e CSRF. A coluna necessária ao CSRF foi adicionada pela revision `0002_session_csrf`.

### `login_attempts`

Registra as informações mínimas usadas pela proteção contra brute force: usuário informado, resultado, horário, endereço de origem observado e prazo do bloqueio, quando houver. Pode ser associado a um usuário identificado, sem armazenar senhas ou cookies. A aplicação já usa esses registros para aplicar o limite transacionalmente no SQLite.

### `app_settings`

Armazena configurações operacionais seguras e editáveis pelo painel, como timezone, retenções e timeouts. Configurações estruturais e secrets permanecem fora dessa entidade.

### `audit_events`

Registra a trilha do que aconteceu no sistema, quem ou qual origem executou e qual foi o resultado. Pode referenciar usuário e job responsáveis, além de ações como `UPDATE_SERVER`, `BACKUP`, `RESTORE`, `BAN`, `UNBAN` e `LOGIN_BLOCKED`. A fundação implementada já registra tentativas de login e o evento de bloqueio; consulta, retenção operacional e auditoria dos demais domínios pertencem às etapas posteriores.

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

A entidade de notificação não duplica toda a auditoria. O schema inicial já restringe estados e o máximo de três tentativas; a lógica de aquisição e entrega pertence às etapas de jobs e Discord.

### `jobs`

Representa operações persistentes em background, incluindo tipo, estado, progresso e referência ao log textual. Pode se relacionar a eventos de auditoria e registros de backup.

A revision `0003_lifecycle_job_guard` adiciona `coordination_key` e um índice único parcial para impedir mais de um job com a mesma chave em `PENDING` ou `RUNNING`. Os jobs de ciclo de vida usam `PALWORLD_LIFECYCLE`; estados terminais liberam a chave para uma nova ação.

### `backup_records`

Cataloga backups gerenciados, sua localização, integridade e estado. Relaciona-se aos jobs que os criam, transferem ou restauram, sem guardar o conteúdo do backup no banco.

### `ban_history`

Mantém o histórico administrativo de Ban e Unban, incluindo alvo, motivo e resultado. Complementa a auditoria, mas não substitui o estado mantido pelo Palworld.

O detalhamento futuro deve preservar transações, integridade referencial e preparação para múltiplos usuários. A migration atual está em `migrations/versions/` e deve ser aplicada com `make db-upgrade`. Consulte os requisitos de banco e auditoria em [SPECIFICATION.md](../../SPECIFICATION.md).
