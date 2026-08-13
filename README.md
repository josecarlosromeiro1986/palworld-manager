# Palworld Manager

Aplicação web planejada para administrar um servidor dedicado de Palworld com segurança, interface privada e baixa dependência de operações rotineiras via terminal.

## Objetivos principais

- Controlar o ciclo de vida do servidor e acompanhar sua saúde.
- Consultar jogadores, enviar anúncios e executar ações administrativas pela API oficial.
- Exibir métricas e logs em tempo real.
- Gerenciar configurações, backups, restaurações e atualizações com fluxos seguros.
- Integrar backups ao Google Drive e alertas ao Discord.
- Manter auditoria, diagnóstico e acesso privado por Tailscale.

## Stack

- **Backend:** Python, FastAPI, Jinja2, SQLite, SQLAlchemy e Alembic.
- **Frontend:** HTMX, Tailwind CSS e Chart.js.
- **Desenvolvimento:** Docker Compose com aplicação web, worker e serviços simulados.
- **Produção:** serviços systemd independentes para web e worker, com Tailscale Serve.
- **Integrações:** REST API oficial do Palworld, SteamCMD, rclone e Discord.

## Status

> Status: Em desenvolvimento. As funcionalidades da V1 estão planejadas, mas ainda não estão implementadas.

## Desenvolvimento

O repositório ainda não possui comandos de desenvolvimento ou validação executáveis. Os comandos `make dev`, `make test` e `make check` serão adicionados durante as etapas de implementação definidas em [SPECIFICATION.md](SPECIFICATION.md).

## Documentação

- [README.md](README.md): entrada rápida do projeto.
- [SPECIFICATION.md](SPECIFICATION.md): fonte de verdade dos requisitos oficiais da V1.
- [docs/index.md](docs/index.md): documentação técnica e operacional.

Em caso de conflito entre qualquer documento e `SPECIFICATION.md`, prevalece `SPECIFICATION.md`.
