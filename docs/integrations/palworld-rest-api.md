# REST API do Palworld

> Status: Implementado para health, consulta manual de jogadores, anúncios e comunicação do desligamento assistido. Kick, Ban e Unban permanecem planejados para a etapa seguinte.

O Palworld Manager usa exclusivamente os endpoints oficiais necessários ao comportamento já implementado:

- `GET /info` para o sinal REST do health check;
- `GET /players` para consultas sob demanda e para operações que precisem verificar jogadores conectados;
- `POST /announce`, com JSON `{"message": "..."}`, para anúncios livres e avisos do desligamento assistido.

Nenhum endpoint de Kick, Ban ou Unban é exposto nesta etapa. O Manager não lê nem modifica saves para descobrir jogadores offline.

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

Development e test selecionam um cliente fake completo no startup, sem exigir credenciais ou abrir rede. Ele simula info, jogadores, anúncios e todas as categorias de falha. O container `mock-services` expõe os três contratos HTTP confirmados para desenvolvimento de integrações sem um servidor Palworld real.

## Jogadores

A página **Jogadores** começa com “Ainda não consultado”. Um `GET` da página apenas lê o estado local; `GET /players` da API oficial só ocorre após o POST autenticado e protegido por CSRF do botão **Atualizar jogadores**, ou quando uma operação interna realmente precisa dessa informação.

O resultado tipado e seu timestamp ficam em um snapshot protegido para acesso concorrente, somente na memória do processo web. Não existe tabela, migration, arquivo ou polling para esse cache. Reiniciar o processo elimina o snapshot e restaura “Ainda não consultado”. A UI mostra nome, conta/plataforma, nível, ping e os IDs oficiais necessários; o endereço IP recebido não é exibido.

## Anúncios

Anúncios aceitam texto livre e não têm agendamento. O formulário mostra o contador de caracteres, exige sessão e CSRF e só envia quando o campo de confirmação repete exatamente a mensagem, incluindo espaços, acentos, pontuação e quebras de linha. Antes do submit, um modal acessível do próprio painel apresenta o texto exato e exige a confirmação final de envio.

Cada tentativa validada é auditada como `PALWORLD_ANNOUNCEMENT`, com usuário, mensagem, destino e resultado. Falhas externas registram somente a categoria segura; credenciais e detalhes de transporte nunca são persistidos.

## Referências oficiais

- [Introdução à REST API](https://docs.palworldgame.com/api/rest-api/palwold-rest-api/)
- [`GET /info`](https://docs.palworldgame.com/api/rest-api/info/)
- [`GET /players`](https://docs.palworldgame.com/api/rest-api/players/)
- [`POST /announce`](https://docs.palworldgame.com/api/rest-api/announce/)
