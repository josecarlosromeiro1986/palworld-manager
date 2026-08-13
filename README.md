# Palworld Manager

Aplicação web em desenvolvimento para administrar um servidor dedicado de Palworld com segurança, interface privada e baixa dependência de operações rotineiras via terminal.

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

> Status: Em desenvolvimento. A base da aplicação, a autenticação segura e o layout administrativo responsivo estão implementados; as funcionalidades operacionais da V1 continuam planejadas conforme a especificação.

## Desenvolvimento

Pré-requisitos: Git, Docker com Docker Compose e GNU Make. Python e as ferramentas de qualidade são fornecidos pela imagem de desenvolvimento.

```bash
make dev
```

A aplicação fica disponível em `http://127.0.0.1:8080` e exige o administrador criado com `make admin-create`; o simulador mínimo fica em `http://127.0.0.1:8090`. Para encerrar os containers, execute `make down`.

```bash
make test
make check
```

O schema local é criado e atualizado explicitamente com `make db-upgrade`. Assets Tailwind, HTMX e ícones são compilados localmente pela imagem; `make frontend-build` permite reconstruí-los separadamente. Consulte a [preparação do ambiente](docs/development/setup.md) para os demais comandos disponíveis. O worker e os serviços simulados ainda são estruturas mínimas, sem jobs ou integrações reais.

## Documentação

- [README.md](README.md): entrada rápida do projeto.
- [SPECIFICATION.md](SPECIFICATION.md): fonte de verdade dos requisitos oficiais da V1.
- [docs/index.md](docs/index.md): documentação técnica e operacional.

Em caso de conflito entre qualquer documento e `SPECIFICATION.md`, prevalece `SPECIFICATION.md`.
