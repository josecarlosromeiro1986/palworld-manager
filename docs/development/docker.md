# Desenvolvimento com Docker

> Status: Em desenvolvimento. Os três containers base, os fakes do Palworld, o sistema persistente de jobs e os contratos REST administrativos estão implementados; integrações adicionais continuam planejadas.

O Docker Compose de desenvolvimento possui três containers e preserva a separação entre aplicação web e worker usada em produção. Todos usam a mesma imagem de desenvolvimento com comandos diferentes.

`app` e `worker` compartilham o volume nomeado `manager-data`, montado em `/var/lib/palworld-manager`. `mock-services` não recebe acesso ao banco do Manager.

## `app`

Compila os assets locais no startup, executa o FastAPI com reload, valida `APP_ENVIRONMENT`, host, porta e caminhos estruturais e publica a porta `8080` somente em `127.0.0.1`. Já fornece `/health`, autenticação, métricas, layout administrativo responsivo, health do Palworld, consulta manual de jogadores, anúncios e criação/acompanhamento dos jobs de Start, Stop e Restart.

## `worker`

Executa um processo Python separado, valida a mesma configuração estrutural e não possui servidor HTTP. Ele adquire jobs atomicamente, mantém heartbeat a cada 10 segundos, executa ciclo de vida, desligamento e backup local, grava logs e backups no volume `manager-data`, recupera jobs abandonados como `INTERRUPTED` sem retomá-los e coordena operações incompatíveis pelo maintenance lock global. Um segundo processo com lease recente é recusado. No ambiente de desenvolvimento, o backup usa payload Palworld fake e nunca lê `PALWORLD_DIR` ou `PALWORLD_SETTINGS` reais.

## `mock-services`

Publica na porta `8090` um serviço simulado com `/health` e os contratos oficiais confirmados `GET /v1/api/info`, `GET /v1/api/players`, `POST /v1/api/announce`, Kick, Ban, Unban e `POST /v1/api/save`. O fake usado diretamente por development e test também permite controlar respostas e falhas sem abrir rede.

Development e test não dependerão de:

- servidor Palworld real;
- webhook real do Discord;
- Google Drive real;
- operações reais de systemd ou journald; web e worker compartilham pelo SQLite um fake do serviço, processo, porta e REST API;
- alterações reais via SteamCMD.

Os simuladores futuros devem permitir cenários de sucesso, timeout e falha sem expor credenciais nem alterar o host.

```bash
make dev
make down
```

Consulte [preparação do ambiente](setup.md) para testes e gates de qualidade.
