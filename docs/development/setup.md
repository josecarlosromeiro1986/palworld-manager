# Preparação do ambiente

> Status: Implementado para desenvolvimento da V1 `1.0.0`.

O desenvolvimento principal usa Docker Compose. A imagem fornece Python, FastAPI, Node.js, npm, Ruff, Mypy, Pytest e pre-commit, sem exigir instalação local dessas ferramentas.

## Requisitos

- Git para versionamento.
- Docker com Docker Compose.
- GNU Make opcional para os atalhos do projeto.
- Node.js e npm são necessários para compilar assets, mas já são fornecidos pela imagem de desenvolvimento.

Não é necessário instalar localmente SQLite, servidores Palworld, rclone, Discord ou serviços systemd usados apenas dentro dos containers ou simuladores.

## Comandos

```bash
make dev
make db-upgrade
make db-current
make admin-create
make admin-reset-password
make frontend-build
make frontend-check
make test
make lint
make format
make typecheck
make precommit
make check
make e2e
make down
```

`make dev` compila os assets e mantém os serviços em primeiro plano. `make frontend-build` recompila Tailwind, HTMX e o sprite de ícones; `make frontend-check` também valida os arquivos JavaScript. `make db-upgrade` aplica migrations pendentes ao SQLite configurado; `make db-current` mostra a revisão atual. Depois da migration, `make admin-create` cria o único administrador da V1 e `make admin-reset-password` redefine sua senha. Ambos solicitam usuário e senha interativamente; a senha é oculta, confirmada e nunca deve ser passada como argumento. `make check` executa o gate de frontend, lint, verificação de formatação, análise estática e Pytest. `make e2e` constrói o estágio Playwright isolado e executa somente os fluxos críticos no Chromium. Sem GNU Make, use `docker compose run --build --rm e2e`.

Os comandos equivalentes da CLI aceitam `--username` opcional, mas sempre leem a senha pelo terminal:

```bash
python -m app.cli create-admin
python -m app.cli reset-password
```

## Ambientes e configuração

`APP_ENVIRONMENT` aceita somente `development`, `test` ou `production`. O
Compose define `development`; `make test` executa a suíte com `test`. Em
produção, as units systemd carregam `production` pelo arquivo estrutural
instalado na Etapa 29.

As configurações estruturais suportadas são:

```text
APP_ENVIRONMENT
PALWORLD_SERVICE
PALWORLD_REST_BASE_URL
PALWORLD_REST_USERNAME
PALWORLD_REST_PASSWORD
PALWORLD_DIR
PALWORLD_SETTINGS
STEAMCMD
RCLONE
RCLONE_REMOTE
DISCORD_WEBHOOK_URL
APP_HOST
APP_PORT
MANAGER_DATABASE
```

Use [`.env.example`](../../.env.example) como referência para desenvolvimento e nunca versione `.env`. Variáveis do processo têm precedência sobre o arquivo. O Compose usa `APP_HOST=0.0.0.0` apenas dentro do container e publica a porta somente em `127.0.0.1` no host.

Web e worker validam a configuração antes de iniciar. Portas inválidas, caminhos relativos, ambientes desconhecidos e bind de produção fora de loopback impedem o startup sem incluir o valor recebido na mensagem de validação.

`MANAGER_DATABASE` deve apontar para um caminho absoluto. O default é `/var/lib/palworld-manager/manager.db`; no Compose, esse caminho usa o volume persistente compartilhado entre web e worker. Testes de integração substituem o valor por arquivos SQLite temporários e isolados.

Configurações operacionais ficam no SQLite. Em produção, a configuração
estrutural vem de `/etc/palworld-manager/manager.env`, e os secrets são
injetados por `/etc/palworld-manager/secrets.env`; nenhum deles usa um `.env`
versionado. O rclone usa
`/var/lib/palworld-manager/rclone/rclone.conf`, protegido separadamente para
permitir renovação de tokens.

Consulte o [runbook de produção](../operations/production-install.md) para venv,
assets, migrations, systemd, Polkit, Tailscale e validação separada da web e do
worker.

Veja também [Docker](docker.md), [testes](testing.md) e o plano incremental em [SPECIFICATION.md](../../SPECIFICATION.md).
