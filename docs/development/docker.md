# Desenvolvimento com Docker

> Status: Em desenvolvimento. Os três containers base e o fake do serviço Palworld estão implementados; jobs e integrações externas continuam planejados.

O Docker Compose de desenvolvimento possui três containers e preserva a separação entre aplicação web e worker usada em produção. Todos usam a mesma imagem de desenvolvimento com comandos diferentes.

`app` e `worker` compartilham o volume nomeado `manager-data`, montado em `/var/lib/palworld-manager`. `mock-services` não recebe acesso ao banco do Manager.

## `app`

Compila os assets locais no startup, executa o FastAPI com reload, valida `APP_ENVIRONMENT`, host, porta e caminhos estruturais e publica a porta `8080` somente em `127.0.0.1`. Já fornece `/health`, autenticação, métricas, layout administrativo responsivo e consulta do estado do serviço Palworld por fake. A criação e o acompanhamento de jobs serão adicionados nas etapas correspondentes.

## `worker`

Executa um processo Python separado, valida a mesma configuração estrutural e não possui servidor HTTP. Ele ainda é um processo de bootstrap com encerramento limpo; fila SQLite, heartbeat e execução de jobs pertencem à Etapa 17.

## `mock-services`

Publica na porta `8090` um serviço mínimo com `/health`. Os contratos simulados da REST API do Palworld, Discord e demais integrações serão adicionados somente quando seus comportamentos forem implementados e confirmados.

Development e test não dependerão de:

- servidor Palworld real;
- webhook real do Discord;
- Google Drive real;
- operações reais de systemd ou journald; a consulta de estado do Palworld usa um fake em memória;
- alterações reais via SteamCMD.

Os simuladores futuros devem permitir cenários de sucesso, timeout e falha sem expor credenciais nem alterar o host.

```bash
make dev
make down
```

Consulte [preparação do ambiente](setup.md) para testes e gates de qualidade.
