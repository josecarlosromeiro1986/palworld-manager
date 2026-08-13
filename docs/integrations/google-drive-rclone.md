# Google Drive com rclone

> Status: Planejado para a V1.

rclone conectará o Manager ao Google Drive. A configuração inicial e a autenticação do remote serão feitas manualmente no terminal; credenciais não serão exibidas nem armazenadas no SQLite.

O Manager trabalhará em uma pasta ou namespace exclusivo para:

- testar conexão e consultar quota;
- enviar backups válidos;
- listar e baixar backups gerenciados;
- obter temporariamente um backup para restore remoto;
- aplicar retenção de até 10 backups próprios.

Antes de um upload, o Manager verificará o armazenamento gratuito disponível. A retenção só poderá remover backups reconhecidos como próprios; se ainda não houver quota, o upload será cancelado e o backup local permanecerá intacto.

> O Palworld Manager nunca pode excluir arquivos externos à sua área de backups.

Downloads para restore serão temporários, validados antes de qualquer alteração e limpos somente quando for seguro. Consulte [backup e restore](../operations/backup-restore.md) e [SPECIFICATION.md](../../SPECIFICATION.md).
