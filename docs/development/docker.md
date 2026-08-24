# Desenvolvimento com Docker

> Status: Em desenvolvimento. Os três containers base, o serviço E2E opt-in, os fakes do Palworld, o sistema persistente de jobs, os contratos REST administrativos e a entrega simulada do Discord estão implementados; integrações adicionais continuam planejadas.

O Docker Compose de desenvolvimento possui três containers base e preserva a separação entre aplicação web e worker usada em produção. `app`, `worker` e `mock-services` usam a imagem normal com comandos diferentes; `e2e` possui perfil e estágio próprios, usados somente sob demanda.

`app` e `worker` compartilham o volume nomeado `manager-data`, montado em `/var/lib/palworld-manager`. `mock-services` não recebe acesso ao banco do Manager.

## `app`

Compila os assets locais no startup, executa o FastAPI com reload, valida `APP_ENVIRONMENT`, host, porta e caminhos estruturais e publica a porta `8080` somente em `127.0.0.1`. Já fornece `/health`, autenticação, métricas, layout administrativo responsivo, health do Palworld, consulta manual de jogadores, anúncios e criação/acompanhamento dos jobs de Start, Stop e Restart.

## `worker`

Executa um processo Python separado, valida a mesma configuração estrutural e não possui servidor HTTP. Ele adquire jobs e notificações atomicamente, mantém heartbeat a cada 10 segundos, executa ciclo de vida, desligamento e backup local, grava logs e backups no volume `manager-data`, recupera jobs abandonados como `INTERRUPTED` sem retomá-los e reconcilia notificações `SENDING` com semântica at least once. Operações incompatíveis usam o maintenance lock global e um segundo processo com lease recente é recusado. No ambiente de desenvolvimento, backup e Discord usam fakes integrais e nunca leem `PALWORLD_DIR` ou `PALWORLD_SETTINGS` reais nem acessam a rede externa.

## `mock-services`

Publica na porta `8090` um serviço simulado com `/health` e os contratos oficiais confirmados `GET /v1/api/info`, `GET /v1/api/players`, `POST /v1/api/announce`, Kick, Ban, Unban e `POST /v1/api/save`. O fake usado diretamente por development e test também permite controlar respostas e falhas sem abrir rede.

## `e2e`

O serviço opt-in instala Chromium somente no estágio Playwright, roda como
`palmanager` e não publica portas. Cada execução cria um SQLite temporário,
aplica migrations e inicia web e worker em `APP_ENVIRONMENT=test`; todos os
adapters externos permanecem fake. Execute `make e2e` ou
`docker compose run --build --rm e2e`. Resultados e traces temporários ficam
dentro do container e são removidos com ele.

Development e test não dependem de:

- servidor Palworld real;
- webhook real do Discord;
- Google Drive real;
- operações reais de systemd ou journald; web e worker compartilham pelo SQLite um fake do serviço, processo, porta e REST API;
- alterações reais via SteamCMD; a verificação e o Update usam um fake integral
  em memória, sem executar processos externos ou acessar `PALWORLD_DIR`.

Os fakes permitem cenários de sucesso, timeout e falha sem expor credenciais nem alterar o host.

```bash
make dev
make down
```

Consulte [preparação do ambiente](setup.md) para testes e gates de qualidade.
