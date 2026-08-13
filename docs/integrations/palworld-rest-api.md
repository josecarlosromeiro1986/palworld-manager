# REST API do Palworld

> Status: Probe de health implementado; operações administrativas permanecem planejadas para a V1.

A integração usará exclusivamente a REST API oficial do Palworld para:

- consultar jogadores online sob demanda, sem polling contínuo;
- enviar anúncios;
- executar Kick, Ban e Unban;
- contribuir para health checks quando aplicável.

O cliente deverá tipar respostas, aplicar timeout, tratar autenticação e distinguir servidor offline, API indisponível, resposta inválida e falha inesperada. Development e test usarão uma implementação simulada.

## Configuração

A URL-base estrutural é configurada separadamente das credenciais:

```text
PALWORLD_REST_BASE_URL=http://127.0.0.1:8212/v1/api
```

Em produção, `PALWORLD_REST_USERNAME` e `PALWORLD_REST_PASSWORD` são secrets obrigatórios, armazenados em `/etc/palworld-manager/secrets.env` com acesso restrito ao usuário `palmanager`. Não há username padrão e a aplicação nunca usa `admin` como fallback. Ausência, valor vazio ou formato inválido impede o startup sem expor a credencial.

Development e test usam um fake completo e não fazem chamadas à API real nem exigem credenciais.

## Health check

O health check usa somente o endpoint oficial `GET /info`, com HTTP Basic Auth e timeout. Uma resposta `200` só é considerada saudável quando o JSON contém os campos de texto esperados `version`, `servername`, `description` e `worldguid`. Falhas de autenticação, indisponibilidade e respostas inválidas são diferenciadas internamente e combinadas com o estado do systemd e do processo.

> O Palworld Manager não tentará descobrir jogadores offline lendo/modificando saves na V1.

Os demais endpoints serão documentados somente após confirmação na documentação oficial vigente. Veja os requisitos de jogadores e administração em [SPECIFICATION.md](../../SPECIFICATION.md).

Referências oficiais consultadas: [REST API do Palworld](https://docs.palworldgame.com/api/rest-api/palwold-rest-api/) e [`GET /info`](https://docs.palworldgame.com/api/rest-api/info/).
