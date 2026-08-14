# REST API do Palworld

> Status: Implementado para health, consulta manual de jogadores, anúncios, Kick, Ban, Unban, salvamento seguro e comunicação do desligamento assistido.

O Palworld Manager usa exclusivamente os endpoints oficiais necessários ao comportamento já implementado:

- `GET /info` para o sinal REST do health check;
- `GET /players` para consultas sob demanda e para operações que precisem verificar jogadores conectados;
- `POST /announce`, com JSON `{"message": "..."}`, para anúncios livres e avisos do desligamento assistido.
- `POST /kick`, com `userid` e `message` opcional, para desconectar um jogador;
- `POST /ban`, com `userid` e `message`, para banir um jogador;
- `POST /unban`, somente com `userid`, para remover um Ban.
- `POST /save`, sem payload, para solicitar o salvamento seguro antes de um backup.

O Manager não lê nem modifica saves para descobrir jogadores offline ou banidos. Como a API oficial de Unban não aceita mensagem, o motivo obrigatório dessa ação permanece somente no histórico administrativo e na auditoria; nenhum campo adicional é inventado no payload remoto.

## Cliente e erros

O adapter de produção usa HTTP Basic Auth e timeout de 5 segundos. A URL-base é estrutural e separada das credenciais; username e password nunca entram na URL. Respostas possuem limite de leitura e são convertidas para tipos internos antes de chegar à UI.

A lista de jogadores valida os campos oficiais `name`, `accountName`, `playerId`, `userId`, `ip`, `ping`, `location_x`, `location_y`, `level` e `building_count`. Os campos acrescentados em versões mais recentes, `accountName` e `building_count`, são opcionais para preservar compatibilidade com versões oficiais anteriores; tipos incompatíveis invalidam toda a resposta.

Falhas são classificadas sem incorporar URL autenticada, headers, corpo remoto ou detalhes da exceção:

- servidor offline por conexão recusada;
- autenticação rejeitada;
- timeout;
- API indisponível;
- resposta ou status inválido;
- falha inesperada.

O health converte essas categorias para seus estados técnicos. A página de jogadores apresenta mensagens seguras e preserva o último snapshot válido quando uma atualização falha.

## Configuração e ambientes

A URL-base estrutural permanece separada das credenciais:

```text
PALWORLD_REST_BASE_URL=http://127.0.0.1:8212/v1/api
```

Em produção, `PALWORLD_REST_USERNAME` e `PALWORLD_REST_PASSWORD` são secrets obrigatórios provenientes de `/etc/palworld-manager/secrets.env`. Não existe username padrão nem fallback para `admin`; valor ausente, vazio ou inválido impede o startup sem revelar a credencial.

Development e test selecionam um cliente fake completo no startup, sem exigir credenciais ou abrir rede. Ele simula info, jogadores, anúncios, Kick, Ban, Unban e todas as categorias de falha. O container `mock-services` expõe os contratos HTTP confirmados para desenvolvimento de integrações sem um servidor Palworld real.

## Jogadores

A página **Jogadores** começa com “Ainda não consultado”. Um `GET` da página apenas lê o estado local; `GET /players` da API oficial só ocorre após o POST autenticado e protegido por CSRF do botão **Atualizar jogadores**, ou quando uma operação interna realmente precisa dessa informação.

O resultado tipado e seu timestamp ficam em um snapshot protegido para acesso concorrente, somente na memória do processo web. Não existe tabela, migration, arquivo ou polling para esse cache. Reiniciar o processo elimina o snapshot e restaura “Ainda não consultado”. A UI mostra nome, conta/plataforma, nível, ping e os IDs oficiais necessários; o endereço IP recebido não é exibido.

## Anúncios

Anúncios aceitam texto livre e não têm agendamento. O formulário mostra o contador de caracteres, exige sessão e CSRF e só envia quando o campo de confirmação repete exatamente a mensagem, incluindo espaços, acentos, pontuação e quebras de linha. Antes do submit, o modal acessível compartilhado do painel apresenta o texto exato e exige a confirmação final de envio.

Cada tentativa validada é auditada como `PALWORLD_ANNOUNCEMENT`, com usuário, mensagem, destino e resultado. Falhas externas registram somente a categoria segura; credenciais e detalhes de transporte nunca são persistidos.

## Administração de jogadores

Kick e Ban ficam disponíveis para jogadores presentes no último snapshot consultado manualmente. Kick aceita motivo opcional; quando informado, ele é enviado como `message`. Ban exige motivo livre e o envia como `message`. Unban recebe manualmente o `userId`, pois este fluxo não consulta saves nem presume uma lista de banidos; seu motivo é obrigatório e registrado localmente.

Os três formulários exigem sessão, CSRF e o modal compartilhado. Sucesso e falha externa criam um registro em `ban_history` e um `audit_event` com ação `KICK`, `BAN` ou `UNBAN`, administrador, alvo, `userId`, motivo e resultado. O histórico recente é exibido na página, mas continua sendo somente a trilha administrativa: a autoridade atual de Ban pertence à API do Palworld.

## Referências oficiais

- [Introdução à REST API](https://docs.palworldgame.com/api/rest-api/palwold-rest-api/)
- [`GET /info`](https://docs.palworldgame.com/api/rest-api/info/)
- [`GET /players`](https://docs.palworldgame.com/api/rest-api/players/)
- [`POST /announce`](https://docs.palworldgame.com/api/rest-api/announce/)
- [`POST /kick`](https://docs.palworldgame.com/api/rest-api/kick/)
- [`POST /ban`](https://docs.palworldgame.com/api/rest-api/ban/)
- [`POST /unban`](https://docs.palworldgame.com/api/rest-api/unban/)
- [`POST /save`](https://docs.palworldgame.com/api/rest-api/save/)
