# Discord

> Status: Implementado na Etapa 23.

A integração usará webhook, sem bot permanente. A política será enxuta: sucessos rotineiros permanecerão na auditoria, enquanto o Discord receberá principalmente falhas e eventos administrativos importantes.

Exemplos previstos incluem crash sem recuperação, falha de backup ou Drive, disco crítico, Update ou Restore concluído/falho, bloqueio de login, job crítico interrompido e encerramento forçado.

O webhook é o secret estrutural `DISCORD_WEBHOOK_URL`, mantido fora do SQLite.
Em production, quando configurado, ele deve usar HTTPS e o endpoint oficial do
Discord, sem query, fragmento ou credenciais adicionais. A ausência não impede
o startup, mas uma entrega pendente termina como falha permanente e controlada;
nenhum evento é descartado silenciosamente. A interface de teste implementada
em **Configurações do Painel** nunca mostra o valor do webhook. Logs, auditoria,
diagnósticos e mensagens de erro também não reproduzem a URL ou o token. A web cria somente
um `notification_event` `DISCORD_TEST` de conteúdo fixo e acompanha seu estado;
a web não acessa o webhook. Solicitações repetidas enquanto o teste está
`PENDING` ou `SENDING` reutilizam o evento ativo.

## Entrega

FastAPI ou worker podem criar um `notification_event` no SQLite. Somente o worker pode consumir o evento e entregar a mensagem ao Discord; FastAPI não acessará o webhook para envio direto.

```text
FastAPI ou Worker
→ notification_events no SQLite
→ Worker
→ Discord
```

Os estados são `PENDING`, `SENDING`, `SENT` e `FAILED`. O claim e o incremento
da tentativa ocorrem atomicamente antes do acesso externo. Falhas transitórias
aguardam 5 segundos depois da primeira tentativa e 30 segundos depois da
segunda. A terceira falha é terminal; uma falha permanente termina já na
primeira tentativa. Não existe loop infinito.

A entrega é at least once. Se o worker reiniciar com um evento deixado em `SENDING`, ele retorna a `PENDING` quando houver menos de 3 tentativas e a próxima entrega conta como nova tentativa; com 3 tentativas, passa a `FAILED`. Como o Discord pode ter aceitado a mensagem antes da interrupção, essa recuperação admite eventual duplicidade.

O adapter envia `POST` JSON com timeout curto. O conteúdo é montado somente a
partir de textos versionados por tipo, IDs numéricos e timestamp UTC; resultados
livres de jobs, nomes de usuário, paths e erros externos não são copiados. O
payload define `allowed_mentions` vazio, limita o tamanho e ignora o corpo da
resposta. Development e test selecionam um fake integral em memória e não abrem
rede.

Os eventos oficiais estão definidos em [SPECIFICATION.md](../../SPECIFICATION.md).
