# Documentação do Palworld Manager

Este diretório explica como o sistema está planejado para ser organizado, desenvolvido e operado. Os requisitos completos da V1 estão em [SPECIFICATION.md](../SPECIFICATION.md), que é a fonte de verdade. Em caso de conflito, prevalece a especificação.

Os estados usados nos documentos são **Planejado**, **Em desenvolvimento** e **Implementado**. Eles devem ser atualizados junto com cada etapa de implementação.

## Arquitetura

- [Visão geral](architecture/overview.md): componentes, integrações e topologia dos serviços web e worker.
- [Métricas](architecture/metrics.md): coleta do host, atualização do Dashboard e retenção somente em memória.
- [Health check do Palworld](architecture/palworld-health.md): combinação de systemd, processo e REST API nos cinco estados do servidor.
- [Controle do servidor](operations/server-lifecycle.md): Start, Restart, desligamento assistido, cancelamento e escalada manual.
- [Logs do servidor](operations/server-logs.md): histórico do journald, filtros e streaming SSE retomável.
- [Segurança](architecture/security.md): modelo de acesso, privilégios, sessões, secrets e execução segura.
- [Modelo de dados](architecture/data-model.md): responsabilidades das entidades persistidas previstas.
- [Jobs e locks](architecture/jobs-and-locks.md): coordenação via SQLite, concorrência, cancelamento e recuperação do worker.

## Desenvolvimento

- [Preparação do ambiente](development/setup.md): requisitos planejados para trabalhar no projeto.
- [Docker](development/docker.md): responsabilidades dos containers de desenvolvimento.
- [Testes](development/testing.md): estratégia de testes e gate de qualidade.
- [Estilo de código](development/code-style.md): ferramentas, organização e fluxo de entrega.
- [Progresso de implementação](development/progress.md): etapa concluída mais recente e próximo trabalho previsto.

## Operações

- [Instalação em produção](operations/production-install.md): arquitetura nativa prevista para Ubuntu Server.
- [Deploy](operations/deploy.md): sequência planejada de publicação e rollback manual.
- [Backup e restore](operations/backup-restore.md): conteúdo, integridade, retenção e restauração segura.
- [Atualizações](operations/updates.md): atualização manual do Palworld via SteamCMD.
- [Configurações do Painel](operations/manager-settings.md): allowlist operacional, limites, troca de senha e testes seguros de integrações.
- [Troubleshooting](operations/troubleshooting.md): índice inicial de investigação de falhas.

## Integrações

- [REST API do Palworld](integrations/palworld-rest-api.md): cliente oficial tipado, consulta manual de jogadores, anúncios, administração de jogadores e tratamento seguro de falhas.
- [Editor do PalWorldSettings.ini](integrations/palworld-settings-ini.md): schema versionado, preservação de desconhecidos, backup pré-save e Restart.
- [Tailscale](integrations/tailscale.md): acesso privado por Tailscale Serve e HTTPS.
- [Google Drive e rclone](integrations/google-drive-rclone.md): armazenamento remoto de backups gerenciados.
- [Discord](integrations/discord.md): webhook e política enxuta de notificações.

## Decisões arquiteturais

- [ADRs](decisions/README.md): finalidade e template para registros de decisões futuras.
