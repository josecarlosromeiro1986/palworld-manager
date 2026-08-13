# Preparação do ambiente

> Status: Em desenvolvimento. O ambiente base e a configuração estrutural estão implementados.

O desenvolvimento principal usa Docker Compose. A imagem fornece Python, FastAPI, Ruff, Mypy, Pytest e pre-commit, sem exigir instalação local dessas ferramentas.

## Requisitos

- Git para versionamento.
- Docker com Docker Compose.
- GNU Make para os atalhos do projeto.
- Node.js e npm somente quando a etapa de frontend passar a compilar assets.

Não é necessário instalar localmente SQLite, servidores Palworld, rclone, Discord ou serviços systemd usados apenas dentro dos containers ou simuladores.

## Comandos

```bash
make dev
make db-upgrade
make db-current
make test
make lint
make format
make typecheck
make precommit
make check
make down
```

`make dev` mantém os serviços em primeiro plano. `make db-upgrade` aplica migrations pendentes ao SQLite configurado; `make db-current` mostra a revisão atual. `make check` executa lint, verificação de formatação, análise estática e testes. `make e2e` está reservado para a Etapa 28 e ainda não executa testes de navegador.

## Ambientes e configuração

`APP_ENVIRONMENT` aceita somente `development`, `test` ou `production`. O Compose define `development`; `make test` executa a suíte com `test`. Em produção, os serviços systemd definirão `production` na etapa de deploy.

As configurações estruturais suportadas são:

```text
APP_ENVIRONMENT
PALWORLD_SERVICE
PALWORLD_DIR
PALWORLD_SETTINGS
STEAMCMD
APP_HOST
APP_PORT
MANAGER_DATABASE
```

Use [`.env.example`](../../.env.example) como referência para desenvolvimento e nunca versione `.env`. Variáveis do processo têm precedência sobre o arquivo. O Compose usa `APP_HOST=0.0.0.0` apenas dentro do container e publica a porta somente em `127.0.0.1` no host.

Web e worker validam a configuração antes de iniciar. Portas inválidas, caminhos relativos, ambientes desconhecidos e bind de produção fora de loopback impedem o startup sem incluir o valor recebido na mensagem de validação.

`MANAGER_DATABASE` deve apontar para um caminho absoluto. O default é `/var/lib/palworld-manager/manager.db`; no Compose, esse caminho usa o volume persistente compartilhado entre web e worker. Testes de integração substituem o valor por arquivos SQLite temporários e isolados.

Configurações operacionais persistidas no SQLite e secrets de integrações serão adicionados nas etapas correspondentes. Em produção, secrets serão injetados no ambiente a partir do arquivo protegido definido na especificação, não por um `.env` versionado.

Veja também [Docker](docker.md), [testes](testing.md) e o plano incremental em [SPECIFICATION.md](../../SPECIFICATION.md).
