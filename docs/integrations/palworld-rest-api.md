# REST API do Palworld

> Status: Probe de health e operações mínimas do desligamento assistido implementados; cliente administrativo completo permanece planejado para a V1.

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

## Desligamento assistido

A Etapa 12 usa somente os contratos oficiais necessários ao fluxo: `GET /players` consulta a quantidade de jogadores uma vez no início e `POST /announce`, com JSON `{"message": "..."}`, envia os avisos. Falhas de autenticação, transporte, status ou formato impedem o Stop para não desligar o servidor sem o aviso previsto. Não há polling contínuo.

O cliente administrativo tipado completo, consulta manual de jogadores e anúncios livres continuam reservados à Etapa 14.

Referências oficiais consultadas: [REST API do Palworld](https://docs.palworldgame.com/api/rest-api/palwold-rest-api/), [`GET /info`](https://docs.palworldgame.com/api/rest-api/info/), [`GET /players`](https://docs.palworldgame.com/api/rest-api/players/) e [`POST /announce`](https://docs.palworldgame.com/api/rest-api/announce/).
