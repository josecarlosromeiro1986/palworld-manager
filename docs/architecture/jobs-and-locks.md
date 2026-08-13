# Jobs e locks

> Status: Jobs de Start, Stop e Restart implementados; fila completa, heartbeat, recovery e maintenance lock geral permanecem planejados para a V1.

Backups, uploads, restores, updates, desligamentos assistidos e outras operações críticas serão jobs persistentes no SQLite. Em produção, o worker será um processo independente da aplicação web.

```text
palworld-manager.service        → FastAPI e interação web
palworld-manager-worker.service → consumo e execução de jobs
SQLite                          → persistência, coordenação e fila
```

O serviço web será responsável por autenticação, sessões, páginas Jinja2, HTMX, SSE, consultas, Dashboard e criação e acompanhamento de jobs. Ele não executará diretamente operações longas ou destrutivas destinadas ao worker.

O worker executará backup, restore, update via SteamCMD, upload ao Google Drive, download de backup remoto, desligamento assistido e outras operações críticas ou demoradas.

O primeiro consumidor implementado é o ciclo de vida do Palworld. A web cria jobs `PALWORLD_START`, `PALWORLD_STOP` e `PALWORLD_RESTART`; o worker os adquire e executa. Uma chave de coordenação com índice único parcial impede duas ações simultâneas, e cada job preserva o timeout vigente no momento da solicitação. Essa fundação é intencionalmente limitada: heartbeat, reconciliação após interrupção, logs textuais e o maintenance lock global serão concluídos na Etapa 17.

Além dos jobs, o worker será o único processo que entrega `notification_events` a integrações externas. FastAPI e worker podem criar esses eventos no SQLite, mas FastAPI não chama o Discord diretamente. A entrega passa pelos estados `PENDING`, `SENDING`, `SENT` ou `FAILED`, usa semântica at least once e permite no máximo 3 tentativas totais para falhas transitórias, sem retry infinito.

## Execução e observabilidade

- O SQLite guardará metadados, estado, progresso e referência ao arquivo de log.
- O worker registrará timestamps, resultado e progresso de cada operação.
- Logs textuais de jobs ficarão separados do banco e não poderão expor secrets.
- A UI mostrará as etapas e indicará quando o cancelamento ainda é seguro.
- Cada transição relevante produzirá auditoria consistente.

A aquisição de um job no SQLite deverá ser atômica e transacional. Mesmo que mais de um worker seja iniciado por erro ou futuramente, um job não poderá ser adquirido e executado duas vezes.

## Health do worker

O worker não exporá servidor HTTP. Ele persistirá um heartbeat no SQLite a cada 10 segundos; sua saúde combinará esse timestamp com o estado de `palworld-manager-worker.service`:

```text
systemd ativo + sem heartbeat + ativação < 30 segundos  → STARTING
systemd ativo + sem heartbeat + ativação >= 30 segundos → UNRESPONSIVE
systemd ativo + heartbeat < 30 segundos                  → HEALTHY
systemd ativo + heartbeat >= 30 segundos                 → UNRESPONSIVE
systemd inativo                                           → OFFLINE
```

O `/health` continuará exclusivo do serviço web.

## Maintenance lock

Um lock global impedirá concorrência entre operações incompatíveis, como Update, Restore, backup pré-update, Restart assistido e ações de energia do host. Durante o lock, leituras seguras, métricas, logs e diagnósticos continuarão disponíveis.

## Cancelamento e recuperação

Contagens regressivas, etapas preparatórias de backup e uploads poderão ser cancelados em pontos definidos. SteamCMD modificando arquivos, Restore substituindo saves e outras etapas de estado incerto não poderão ser interrompidas pela UI.

O reinício do serviço web não implica reinício do worker. Um job já em execução continuará de forma independente quando isso for seguro.

Se o worker reiniciar durante um job, localizará jobs ainda marcados como `running`, mudará ou reconciliará seu estado como `interrupted` e verificará o estado real do sistema. Operações destrutivas nunca serão retomadas automaticamente; situações ambíguas dependerão de decisão humana.

No mesmo startup, eventos de notificação deixados em `SENDING` pelo processo anterior serão reconciliados. Com menos de 3 tentativas, voltam a `PENDING` e a próxima entrega conta como nova tentativa; com 3 tentativas, passam a `FAILED`. Essa recuperação pode duplicar uma mensagem já aceita pelo Discord antes da interrupção, comportamento esperado da semântica at least once.

Redis, Celery, RabbitMQ e Kafka não fazem parte da V1 porque a fila local persistida e um worker independente atendem ao escopo de uma instalação pequena, evitando serviços adicionais em produção. Consulte [SPECIFICATION.md](../../SPECIFICATION.md) para os fluxos e critérios de aceite completos.
