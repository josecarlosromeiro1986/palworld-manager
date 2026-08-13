# Preparação do ambiente

> Status: Planejado para a V1.

O desenvolvimento principal será feito com Docker Compose. No momento, os arquivos e comandos do ambiente ainda não foram implementados.

## Requisitos planejados

- Git para versionamento.
- Docker com Docker Compose para aplicação, banco em volume e serviços simulados.
- Node.js e npm somente quando necessário para instalar ou compilar assets do frontend.
- Ferramentas do projeto, como Ruff, Mypy, Pytest e pre-commit, executadas pelo fluxo definido no repositório.

Não será necessário instalar localmente SQLite, servidores Palworld, rclone, Discord ou serviços systemd usados apenas dentro dos containers ou simuladores. Pré-requisitos e comandos exatos serão registrados aqui quando a etapa de bootstrap os criar.

Veja também [Docker](docker.md), [testes](testing.md) e o plano incremental em [SPECIFICATION.md](../../SPECIFICATION.md).
