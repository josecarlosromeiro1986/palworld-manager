# Discord

> Status: Planejado para a V1.

A integração usará webhook, sem bot permanente. A política será enxuta: sucessos rotineiros permanecerão na auditoria, enquanto o Discord receberá principalmente falhas e eventos administrativos importantes.

Exemplos previstos incluem crash sem recuperação, falha de backup ou Drive, disco crítico, Update ou Restore concluído/falho, bloqueio de login, job crítico interrompido e encerramento forçado.

O webhook será tratado como secret. A interface poderá testar ou substituir a configuração, mas nunca mostrar o valor completo. Logs, auditoria, diagnósticos e mensagens de erro também deverão mascará-lo.

## Entrega

FastAPI ou worker podem criar um `notification_event` no SQLite. Somente o worker pode consumir o evento e entregar a mensagem ao Discord; FastAPI não acessará o webhook para envio direto.

```text
FastAPI ou Worker
→ notification_events no SQLite
→ Worker
→ Discord
```

Os estados conceituais são `PENDING`, `SENDING`, `SENT` e `FAILED`. Falhas transitórias permitem no máximo 3 tentativas totais, com pequeno backoff. Depois disso, o evento fica `FAILED`, sem loop infinito de retry. Os detalhes do backoff serão definidos durante a implementação.

A entrega é at least once. Se o worker reiniciar com um evento deixado em `SENDING`, ele retorna a `PENDING` quando houver menos de 3 tentativas e a próxima entrega conta como nova tentativa; com 3 tentativas, passa a `FAILED`. Como o Discord pode ter aceitado a mensagem antes da interrupção, essa recuperação admite eventual duplicidade.

Os eventos oficiais estão definidos em [SPECIFICATION.md](../../SPECIFICATION.md).
