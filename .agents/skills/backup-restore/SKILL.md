---
name: backup-restore
description: Implementar ou revisar backup, retenção, Google Drive/rclone e restore do Palworld Manager. Usar sempre que uma mudança tocar arquivos de mundo, tar.gz, integridade ou restauração.
---

# Backup e restore

Área crítica. Consulte [backup e restore](../../../docs/operations/backup-restore.md), [Google Drive](../../../docs/integrations/google-drive-rclone.md) e [SPECIFICATION.md](../../../SPECIFICATION.md).

1. Crie `.tar.gz` com `manifest.json`, SHA-256 e teste de integridade antes de marcar o backup como válido.
2. Inclua o mundo completo, `Players/`, configurações relevantes, cópia consistente do SQLite e configurações não sensíveis.
3. Nunca inclua secrets, tokens, webhooks, credenciais, binários, SteamCMD ou a própria área de backups recursivamente.
4. Aplique retenção somente aos backups reconhecidos como gerenciados pelo Manager.
5. Use rclone para Google Drive e nunca remova arquivos externos à pasta ou namespace administrado.
6. Antes do restore, valide origem, manifest, hash, integridade, espaço e formato.
7. Proteja criação e extração contra path traversal, paths absolutos e symlinks perigosos.
8. Exija a confirmação exata `RESTAURAR`, adquira o maintenance lock e crie backup preventivo antes de alterar o mundo.
9. Execute stop seguro, restore, permissões/ownership, start e health check pós-restore.
10. Não faça rollback automático; preserve o backup preventivo e exija decisão humana diante de falha ambígua.
11. Nunca restaure jogador individualmente na V1.
12. Adicione testes fortes de integridade, traversal, symlinks, retenção, namespace remoto, falhas parciais e recuperação.
