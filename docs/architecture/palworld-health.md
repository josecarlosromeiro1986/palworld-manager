# Health check do Palworld

> Status: Implementado.

O estado do servidor combina três sinais independentes:

- `ActiveState` da unidade configurada em `PALWORLD_SERVICE`;
- existência do processo indicado por `MainPID`, confirmada com `psutil`;
- resposta autenticada e válida de `GET /info` na REST API oficial.

Nenhum sinal isolado declara o servidor online. O Dashboard consulta o resultado agregado via HTMX a cada 5 segundos.

## Matriz de estados

| systemd | Processo | REST API | Estado |
| --- | --- | --- | --- |
| `active` | ativo | disponível e válida | `ONLINE` |
| `activating` | qualquer | qualquer | `INICIANDO` |
| `active` | ativo | indisponível, não autorizada, inválida ou falha | `DEGRADADO` |
| `deactivating` | ativo | qualquer | `DEGRADADO` |
| `deactivating` | inativo | disponível | `DEGRADADO` |
| `inactive` | inativo | indisponível | `OFFLINE` |
| `deactivating` | inativo | indisponível, não autorizada, inválida ou falha | `OFFLINE` |
| `failed` | qualquer | qualquer | `FALHA` |
| `active` | inativo | qualquer | `FALHA` |
| `inactive` | ativo ou REST disponível | qualquer | `FALHA` |
| consulta de systemd/processo falha | desconhecido | não consultada | `FALHA` |

Estados incomuns do systemd são tratados de forma conservadora: somente um processo ativo com REST API saudável resulta em `DEGRADADO`; as demais combinações resultam em `FALHA`.

## Fronteiras externas

Em production, as consultas ao systemd usam os executáveis e argumentos fixos documentados, sem shell. O processo é associado ao serviço pelo `MainPID`; não há busca por nomes arbitrários de executável.

O probe REST usa timeout de 5 segundos, HTTP Basic Auth e limita `/info` a 64 KiB. O JSON deve conter `version`, `servername`, `description` e `worldguid` como texto. Autenticação rejeitada, indisponibilidade, resposta inválida e falha inesperada permanecem distinguíveis no resultado do componente, sem expor credenciais ou detalhes internos.

Em development e test, systemd, processo e REST API são integralmente substituídos por fakes controláveis. Esses ambientes não exigem credenciais e não acessam o host nem a rede para calcular o estado.

O mesmo agregador será reutilizado pelas operações de ciclo de vida, update, restore e diagnóstico nas etapas correspondentes.
