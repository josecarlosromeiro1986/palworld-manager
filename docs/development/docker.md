# Desenvolvimento com Docker

> Status: Planejado para a V1.

O Docker Compose de desenvolvimento terá três containers planejados, preservando a separação entre aplicação web e worker usada em produção.

## `app`

Executará o FastAPI e suas ferramentas de desenvolvimento. Criará e acompanhará jobs, sem executar diretamente operações longas ou destrutivas destinadas ao worker.

## `worker`

Consumirá e executará os jobs persistidos no SQLite. Poderá usar a mesma imagem do container `app`, iniciada com um comando diferente. `app` e `worker` compartilharão o SQLite e o volume apropriado ao ambiente de desenvolvimento.

## `mock-services`

Simulará integrações externas com respostas controláveis para desenvolvimento e testes, incluindo a REST API do Palworld, Discord e serviços equivalentes necessários aos fluxos.

Development e test não dependerão de:

- servidor Palworld real;
- webhook real do Discord;
- Google Drive real;
- operações reais de systemd ou journald;
- alterações reais via SteamCMD.

Os simuladores devem permitir cenários de sucesso, timeout e falha sem expor credenciais nem alterar o host. A configuração e os comandos exatos serão documentados quando `docker-compose.yml` e o `Makefile` existirem.
