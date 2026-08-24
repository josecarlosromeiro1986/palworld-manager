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

Armazena exclusivamente a allowlist de configurações operacionais seguras da
página **Configurações do Painel**: backup diário, horário, timezone, retenções,
intervalo das métricas, aviso assistido, limites de disco e timeouts. A gravação
valida tipos, limites e versão concorrente; a auditoria recebe apenas nomes de
chaves. Configurações estruturais e secrets permanecem fora dessa entidade. A
Etapa 24 reutiliza a entidade existente e não exige migration.

### `audit_events`

Registra a trilha do que aconteceu no sistema, quem ou qual origem executou e qual foi o resultado. Pode referenciar usuário e job responsáveis, além de ações como `UPDATE_SERVER`, `BACKUP`, `RESTORE`, `BAN`, `UNBAN` e `LOGIN_BLOCKED`. Os eventos distinguem origem administrativa, automática e de sistema, além de sucesso, falha, cancelamento e interrupção. A duração é derivada dos timestamps do job relacionado quando disponível.

A página autenticada oferece filtros combináveis de período, ação, resultado, origem, usuário e alvo, ordenação decrescente e páginas fixas de 50 registros. A retenção remove eventos com mais de 90 dias durante gravações e consultas. Campos textuais e detalhes estruturados passam por redação defensiva e limites antes da persistência; a leitura também protege valores estruturais sensíveis configurados no ambiente. `PALWORLD_SETTINGS_UPDATE` registra apenas nomes das chaves, versão do schema, nome da cópia pré-save e categoria segura de erro; valores do arquivo e caminhos absolutos não entram no SQLite.

### `notification_events`

Representa eventos destinados a uma integração de notificação externa, inicialmente Discord. Qualquer componente pode criar um evento no SQLite, mas somente o worker pode realizar a entrega externa. FastAPI nunca envia diretamente ao Discord.

Guarda somente as informações necessárias para controlar e registrar a entrega: tipo do evento, canal, status, horário, job relacionado, número de tentativas e erro de entrega, quando houver. Os estados conceituais são:

```text
PENDING → SENDING → SENT
                  ↘ FAILED
```

Falhas transitórias permitem no máximo 3 tentativas totais. A primeira falha
agenda `next_attempt_at` para 5 segundos depois, a segunda para 30 segundos e a
terceira permanece `FAILED`; falhas permanentes terminam na primeira tentativa.
O schema inicial já contém todos os campos e constraints necessários, portanto
a Etapa 23 não exige migration.

A entrega segue semântica at least once. No startup, o worker reconcilia eventos deixados em `SENDING`: com menos de 3 tentativas, retornam a `PENDING` e uma nova tentativa será contabilizada; com 3 tentativas, passam a `FAILED`. Se a entrega externa tiver ocorrido antes da interrupção, mas `SENT` não tiver sido persistido, a nova tentativa pode produzir uma mensagem duplicada.

`last_error` recebe somente uma categoria controlada; URL, token, headers, corpo
remoto e exceções livres não são persistidos. O conteúdo enviado é derivado do
tipo allowlisted e de IDs/timestamps já presentes, sem adicionar payload livre
ou duplicar a auditoria.

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

A revision `0004_assisted_shutdown_controls` adiciona os flags persistentes `cancel_requested` e `execute_now_requested`. Eles permitem que a web solicite mudanças durante a contagem sem executar a operação no processo FastAPI; o worker observa os flags e fecha `is_cancellable` antes do Stop.

A revision `0005_persistent_job_system` adiciona `step`, a tabela singleton `worker_heartbeats` e `maintenance_locks`. O heartbeat guarda a identidade, início e último sinal do worker e também impede uma segunda identidade enquanto o lease de 30 segundos estiver válido. O lock global referencia o job proprietário e é adquirido na mesma transação do claim. Logs textuais permanecem fora do SQLite; `jobs.log_path` guarda somente a referência relativa sob `jobs/<ano>/`.

### `backup_records`

Cataloga backups gerenciados, sua localização relativa, integridade e estado. O backup local implementado cria o registro somente após salvar, montar, validar e publicar o arquivo; falhas não deixam registro `VALID`. Cada registro local referencia o job que o criou, usa `location=LOCAL`, `status=VALID`, tamanho, nome gerenciado e path relativo sob `backups/`, sem expor paths estruturais na interface.

Para backups locais válidos, `sha256` representa exclusivamente o hash dos bytes
do `.tar.gz` final. A integridade interna permanece no `manifest.json`, que lista
deterministicamente cada arquivo do payload com path relativo, tamanho e hash
individual; o manifest e o hash externo não entram nessa lista.

O Restore normal referencia um `backup_record` local válido e preserva o banco
ativo do Manager. Mesmo presentes e validados no artefato,
`manager/manager.db` e `manager/settings.json` são reservados à recuperação
manual/offline de desastre e não alteram usuários, sessões, auditoria, jobs ou
`app_settings` durante o Restore pelo painel.

A revision `0006_drive_backup_locations` troca a unicidade global do nome por
unicidade composta de `location` e `filename`. Assim, o mesmo artefato pode ter
uma linha `LOCAL` e outra `DRIVE`, ambas com tamanho e SHA-256 idênticos, sem
confundir as duas cópias. Registros remotos guardam somente o nome relativo no
namespace fixo; remote, paths estruturais e credenciais não são persistidos. A
retenção e a exclusão remotas só reconhecem linhas `DRIVE` e `VALID` com formato
gerenciado.

O Restore remoto referencia diretamente uma dessas linhas `DRIVE`/`VALID` e
preserva sua identidade, tamanho e SHA-256 no resultado seguro do job. O
download de staging não cria uma linha `LOCAL`, não altera a retenção local e
não remove nem muda o status do registro remoto. Somente um download solicitado
explicitamente como cópia local cria o par `LOCAL` correspondente.

### `ban_history`

Mantém o histórico administrativo implementado de Kick, Ban e Unban, incluindo ação, alvo, `userId`, administrador, motivo, resultado e timestamp. Complementa a auditoria, mas não substitui o estado mantido pelo Palworld. A página de jogadores exibe até 50 registros recentes; a página geral de auditoria oferece filtros e retenção de 90 dias para seus próprios eventos.

O detalhamento futuro deve preservar transações, integridade referencial e preparação para múltiplos usuários. A migration atual está em `migrations/versions/` e deve ser aplicada com `make db-upgrade`. Consulte os requisitos de banco e auditoria em [SPECIFICATION.md](../../SPECIFICATION.md).
