# Desenvolvimento com Docker

> Status: Em desenvolvimento. Os três containers base estão implementados; jobs e integrações continuam planejados.

O Docker Compose de desenvolvimento possui três containers e preserva a separação entre aplicação web e worker usada em produção. Todos usam a mesma imagem de desenvolvimento com comandos diferentes.

## `app`

Executa o FastAPI com reload e publica a porta `8080` somente em `127.0.0.1`. Nesta etapa, fornece apenas o `/health` mínimo. A criação e o acompanhamento de jobs serão adicionados nas etapas correspondentes.

## `worker`

Executa um processo Python separado, sem servidor HTTP. Na Etapa 1 ele é apenas um processo de bootstrap com encerramento limpo; fila SQLite, heartbeat e execução de jobs pertencem à Etapa 17.

## `mock-services`

Publica na porta `8090` um serviço mínimo com `/health`. Os contratos simulados da REST API do Palworld, Discord e demais integrações serão adicionados somente quando seus comportamentos forem implementados e confirmados.

Development e test não dependerão de:

- servidor Palworld real;
- webhook real do Discord;
- Google Drive real;
- operações reais de systemd ou journald;
- alterações reais via SteamCMD.

Os simuladores futuros devem permitir cenários de sucesso, timeout e falha sem expor credenciais nem alterar o host.

```bash
make dev
make down
```

Consulte [preparação do ambiente](setup.md) para testes e gates de qualidade.
