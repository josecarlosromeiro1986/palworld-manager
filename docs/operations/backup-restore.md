# Backup e restore

> Status: Planejado para a V1.

Este documento resume a operação prevista. [SPECIFICATION.md](../../SPECIFICATION.md) contém os requisitos oficiais completos.

## Backup

O backup automático será diário às `04:00` no timezone configurável. O Manager manterá 3 backups locais e até 10 backups próprios no Google Drive.

Cada backup será um `.tar.gz` com `manifest.json`, hash SHA-256 e teste de integridade. Só será considerado válido após essas verificações. Antes da cópia, o Manager solicitará um salvamento seguro pelo mecanismo oficial disponível; uma falha nessa etapa invalida a operação.

O conteúdo previsto inclui:

- mundo completo, inclusive `Players/` e demais dados persistentes;
- configurações relevantes do Palworld;
- cópia consistente do SQLite do Manager;
- configurações não sensíveis do Manager;
- manifest com os metadados necessários à validação.

O backup excluirá:

- secrets, tokens, credenciais e webhooks;
- binários do servidor e SteamCMD;
- a própria área de backups e qualquer cópia recursiva;
- conteúdo não pertencente ao conjunto explicitamente gerenciado.

## Google Drive

rclone fará uploads e downloads em uma pasta ou namespace exclusivo do Palworld Manager. Antes do upload, o sistema verificará quota e aplicará retenção somente aos próprios backups. Se o espaço gratuito continuar insuficiente, o upload será cancelado, o backup local será preservado e a falha será auditada.

**O Manager nunca excluirá arquivos externos à área de backups que administra.** Nenhum plano pago será requisito.

## Restore

O restore local ou remoto seguirá o fluxo:

```text
maintenance lock
→ validação do backup
→ download temporário, se remoto
→ SHA-256 e integridade
→ backup preventivo
→ stop seguro
→ substituição dos arquivos
→ ownership e permissões
→ start
→ REST API e health check
→ conclusão ou falha auditada
```

A operação exigirá a confirmação exata `RESTAURAR`. O arquivo será validado contra traversal, symlinks perigosos, formato inválido e espaço insuficiente antes de alterar o mundo. Temporários só serão removidos quando for seguro.

Não haverá rollback automático. Em caso de falha, o backup preventivo será preservado e a recuperação dependerá de decisão humana. A V1 não restaurará um jogador isoladamente.
