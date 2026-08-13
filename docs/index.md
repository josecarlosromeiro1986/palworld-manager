# Documentação do Palworld Manager

Este diretório explica como o sistema está planejado para ser organizado, desenvolvido e operado. Os requisitos completos da V1 estão em [SPECIFICATION.md](../SPECIFICATION.md), que é a fonte de verdade. Em caso de conflito, prevalece a especificação.

Os estados usados nos documentos são **Planejado**, **Em desenvolvimento** e **Implementado**. Eles devem ser atualizados junto com cada etapa de implementação.

## Arquitetura

- [Visão geral](architecture/overview.md): componentes, integrações e topologia dos serviços web e worker.
- [Métricas](architecture/metrics.md): coleta do host, atualização do Dashboard e retenção somente em memória.
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
- [Troubleshooting](operations/troubleshooting.md): índice inicial de investigação de falhas.

## Integrações

- [REST API do Palworld](integrations/palworld-rest-api.md): escopo da integração administrativa oficial.
- [Tailscale](integrations/tailscale.md): acesso privado por Tailscale Serve e HTTPS.
- [Google Drive e rclone](integrations/google-drive-rclone.md): armazenamento remoto de backups gerenciados.
- [Discord](integrations/discord.md): webhook e política enxuta de notificações.

## Decisões arquiteturais

- [ADRs](decisions/README.md): finalidade e template para registros de decisões futuras.
