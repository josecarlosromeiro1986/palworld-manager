# Configurações do Painel

> Status: Implementado na Etapa 24.

A rota autenticada `/manager-settings` edita somente uma allowlist fechada de
parâmetros operacionais. Toda gravação exige CSRF, valida tipos e limites no
backend e usa uma versão opaca do formulário para rejeitar sobrescritas de uma
página desatualizada. As alterações ficam em `app_settings`; defaults continuam
valendo quando uma chave ainda não foi persistida.

## Parâmetros operacionais

| Parâmetro | Default | Valores aceitos |
| --- | ---: | --- |
| Backup automático | habilitado | habilitado ou desabilitado |
| Horário do backup | `04:00` | `HH:MM`, com precisão de minutos |
| Timezone | `America/Sao_Paulo` | identificador IANA válido |
| Retenção local | 3 | 1 a 30 |
| Retenção no Drive | 10 | 1 a 100 |
| Intervalo das métricas | 5 s | 1 a 60 s |
| Aviso assistido | 5 min | agora, 1, 5 ou 10 minutos |
| Timeout de Start | 120 s | 1 a 600 s |
| Timeout de Restart | 120 s | 1 a 600 s |
| Timeout de Stop | 60 s | 1 a 300 s |
| Aviso de disco livre | 20 GB | 1 a 1024 GB |
| Disco livre crítico | 10 GB | 1 a 1024 GB e menor que o aviso |

Backup, agendamento, retenções, métricas, desligamento assistido, limites de
disco e timeouts consultam os valores no SQLite nos pontos de execução já
existentes. Um valor persistido inválido falha de forma explícita em vez de ser
coagido silenciosamente.

## Limites de segurança

Sudoers, serviços, executáveis, infraestrutura Tailscale, paths estruturais,
remotes e namespaces não são campos da página. Webhook, tokens, credenciais,
cookies, senhas e conteúdo de arquivos de secrets não são exibidos nem
persistidos em `app_settings`. A auditoria de uma atualização registra somente
os nomes das chaves alteradas ou uma categoria controlada de falha, nunca os
valores.

O teste Discord cria um `notification_event` de conteúdo fixo. Somente o worker
consome esse evento e acessa o webhook. O teste Google Drive cria um job
persistente `DRIVE_CHECK`; somente o worker consulta conexão e quota. Reenvios
enquanto a mesma verificação está ativa reutilizam o evento ou job existente.
Development e test usam os fakes integrais dessas duas integrações, sem rede,
rclone, Palworld, systemd ou filesystem estrutural real.

## Conta e usuários

A troca da própria senha foi centralizada em **Minha conta**, disponível para
ambos os papéis. Criação, papel, status e reset administrativo ficam na página
**Usuários**, exclusiva de `ADMIN`. Consulte
[Usuários e controle de acesso](user-management.md).

Consulte também [segurança](../architecture/security.md),
[backup e restore](backup-restore.md), [métricas](../architecture/metrics.md),
[Discord](../integrations/discord.md) e
[Google Drive com rclone](../integrations/google-drive-rclone.md).
