# Jobs e locks

> Status: Fila persistente, worker separado, aquisição atômica, heartbeat, recovery, logs de jobs e maintenance lock global implementados.

Desligamentos, ações de ciclo de vida, backups, restores locais e transferências do Google Drive usam jobs persistentes no SQLite. Updates reutilizarão a mesma infraestrutura na etapa correspondente. Em produção, o worker é um processo independente da aplicação web.

```text
palworld-manager.service        → FastAPI e interação web
palworld-manager-worker.service → consumo e execução de jobs
SQLite                          → persistência, coordenação e fila
```

O serviço web é responsável por autenticação, sessões, páginas Jinja2, HTMX, SSE, consultas, Dashboard e criação e acompanhamento de jobs. Ele não executa diretamente operações longas ou destrutivas destinadas ao worker.

O worker executa as operações suportadas por handlers explícitos. `LOCAL_BACKUP` usa a chave de coordenação própria `LOCAL_BACKUP`, impedindo duas solicitações simultâneas, e exige o lock `GLOBAL` para não concorrer com operações incompatíveis. `LOCAL_RESTORE` usa sua própria chave contra double-submit, também exige o lock `GLOBAL` e nunca é executado pela web. `DRIVE_UPLOAD`, `DRIVE_DOWNLOAD` e `DRIVE_DELETE` usam chaves por artefato e o lock global; `DRIVE_CHECK` apenas consulta conexão/quota e não toma o maintenance lock. Update via SteamCMD será registrado somente na etapa própria.

O primeiro consumidor implementado é o ciclo de vida do Palworld. A web cria jobs `PALWORLD_START`, `PALWORLD_STOP` e `PALWORLD_RESTART`; o worker os adquire e executa. Uma chave de coordenação com índice único parcial impede duas solicitações simultâneas do mesmo domínio, e cada job preserva o timeout vigente no momento da solicitação.

O desligamento usa `PALWORLD_ASSISTED_SHUTDOWN`, `PALWORLD_FORCE_SIGTERM` e `PALWORLD_FORCE_SIGKILL` sob a mesma chave. A contagem grava progresso e pedidos de cancelamento ou execução imediata no SQLite. O worker fecha o ponto de cancelamento antes de iniciar o Stop. SIGKILL nunca é criado como consequência automática: somente uma nova requisição autenticada, após falha de SIGTERM, pode enfileirá-lo.

Além dos jobs, o worker será o único processo que entrega `notification_events` a integrações externas. FastAPI e worker já podem criar esses eventos no SQLite, mas FastAPI não chama o Discord diretamente. Aquisição, entrega e reconciliação de notificações permanecem reservadas à Etapa 23.

## Execução e observabilidade

- O SQLite guarda metadados, estado, etapa, progresso, timestamps, resultado e referência ao arquivo de log.
- Os logs textuais ficam em `jobs/<ano>/<tipo>-<id>.log`, ao lado do banco, e são retidos por 90 dias.
- O log registra apenas mensagens operacionais controladas; detalhes de exceções e secrets não são copiados.
- O Dashboard acompanha estado, etapa, progresso e o trecho recente do log.
- Cada transição relevante produz auditoria consistente.

A aquisição usa uma única atualização condicional no SQLite. Uma corrida real entre consumidores entrega o job a apenas um deles. O heartbeat também funciona como lease: uma identidade diferente com heartbeat de menos de 30 segundos é recusada, impedindo que um segundo processo recupere jobs de um worker ainda vivo.

## Health do worker

O worker não expõe servidor HTTP. Ele persiste um heartbeat no SQLite a cada 10 segundos; sua saúde combina esse timestamp com o estado e o tempo de ativação de `palworld-manager-worker.service`:

```text
systemd ativo + sem heartbeat + ativação < 30 segundos  → STARTING
systemd ativo + sem heartbeat + ativação >= 30 segundos → UNRESPONSIVE
systemd ativo + heartbeat < 30 segundos                  → HEALTHY
systemd ativo + heartbeat >= 30 segundos                 → UNRESPONSIVE
systemd inativo                                           → OFFLINE
```

O Dashboard consulta esse estado a cada 10 segundos. O `/health` continua exclusivo do serviço web.

## Maintenance lock

O worker adquire a linha `GLOBAL` de `maintenance_locks` na mesma transação do claim de um job incompatível. Outros jobs que exigem o lock permanecem `PENDING`; trabalhos sem lock podem continuar. O lock é liberado ao terminar e locks órfãos de jobs terminais são removidos com segurança. Backup e Restore locais já usam essa coordenação; Update, backup pré-update e ações de energia usarão a mesma infraestrutura quando forem implementados. Leituras seguras, métricas, logs e diagnósticos não dependem do lock.

## Cancelamento e recuperação

Contagens regressivas, backups locais, uploads e downloads podem ser cancelados nos checkpoints seguros implementados. O backup local fecha o cancelamento antes da publicação atômica; transferências remotas fecham antes de publicar a cópia validada. Exclusão remota não é cancelável. O Restore local é não cancelável desde a criação: sua UI informa essa condição e nenhuma rota de cancelamento é oferecida. SteamCMD modificando arquivos, Restore substituindo saves e outras etapas de estado incerto não poderão ser interrompidas pela UI.

O reinício do serviço web não implica reinício do worker. Um job já em execução continuará de forma independente quando isso for seguro.

Se o worker reiniciar após perder o lease anterior, localiza jobs ainda em `RUNNING`, muda cada um para `INTERRUPTED`, encerra o ponto de cancelamento, libera o lock e registra auditoria e log. Nenhum job interrompido é recolocado na fila. A UI exige revisão manual e o health atual do Palworld permite verificar o estado real antes de uma nova ação.

A reconciliação de eventos de notificação deixados em `SENDING` continua planejada para a Etapa 23, junto da entrega ao Discord; a Etapa 17 não antecipa esse consumidor.

Redis, Celery, RabbitMQ e Kafka não fazem parte da V1 porque a fila local persistida e um worker independente atendem ao escopo de uma instalação pequena, evitando serviços adicionais em produção. Consulte [SPECIFICATION.md](../../SPECIFICATION.md) para os fluxos e critérios de aceite completos.
