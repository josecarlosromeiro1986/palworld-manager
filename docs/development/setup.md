# Preparação do ambiente

> Status: Em desenvolvimento. O ambiente base da Etapa 1 está implementado.

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
make test
make lint
make format
make typecheck
make precommit
make check
make down
```

`make dev` mantém os serviços em primeiro plano. `make check` executa lint, verificação de formatação, análise estática e testes. `make e2e` está reservado para a Etapa 28 e ainda não executa testes de navegador.

Veja também [Docker](docker.md), [testes](testing.md) e o plano incremental em [SPECIFICATION.md](../../SPECIFICATION.md).
