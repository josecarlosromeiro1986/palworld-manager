---
name: jobs-worker
description: Implementar ou revisar jobs persistentes, worker, locks, heartbeat e entrega de notificações do Palworld Manager. Usar em fila SQLite, concorrência, cancelamento, recuperação ou notification_events.
---

# Jobs e worker

Consulte [jobs e locks](../../../docs/architecture/jobs-and-locks.md), o [modelo de dados](../../../docs/architecture/data-model.md) e [SPECIFICATION.md](../../../SPECIFICATION.md).

1. Preserve os processos separados: `palworld-manager.service` para web e `palworld-manager-worker.service` para execução de jobs.
2. Use SQLite como persistência, coordenação e fila da V1. Não introduza Redis, Celery, RabbitMQ ou Kafka sem nova decisão arquitetural.
3. Adquira jobs de forma atômica e transacional para impedir execução duplicada.
4. Aplique maintenance lock global às operações incompatíveis e mantenha transições de estado consistentes.
5. Projete idempotência onde aplicável, sem mascarar operações destrutivas ou de estado incerto.
6. Permita cancelamento somente em pontos seguros e informe quando a operação deixar de ser cancelável.
7. Após reinício do worker, reconcilie jobs `running` como `interrupted` conforme o estado real. Nunca retome automaticamente operação destrutiva ambígua.
8. Atualize o heartbeat no SQLite a cada 10 segundos. Sem heartbeat, classifique serviço ativo como `STARTING` com menos de 30 segundos desde a ativação e `UNRESPONSIVE` a partir de 30 segundos; com heartbeat, use `HEALTHY` abaixo de 30 segundos e `UNRESPONSIVE` a partir de 30 segundos; serviço inativo é `OFFLINE`.
9. Não crie servidor HTTP no worker; `/health` pertence somente à aplicação web.
10. Permita que qualquer componente crie `notification_events`, mas deixe a entrega externa exclusivamente com o worker. FastAPI não envia diretamente ao Discord.
11. Use `PENDING`, `SENDING`, `SENT` e `FAILED` com entrega at least once; no startup, retorne `SENDING` a `PENDING` se houver tentativa disponível, ou marque `FAILED` após 3 tentativas. Aceite possível duplicidade e nunca crie retry infinito.
12. Registre estado, progresso, timestamps, resultado e log sem secrets. Cubra concorrência, interrupção, locks, heartbeat e retries com testes fortes.
