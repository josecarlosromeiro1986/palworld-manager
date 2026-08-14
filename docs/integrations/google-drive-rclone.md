# Google Drive com rclone

> Status: Implementado para backup remoto; Restore remoto permanece na Etapa 21.

rclone conecta o Manager ao Google Drive. A configuração inicial e a autenticação do remote são feitas manualmente no terminal, sob o mesmo usuário `palmanager` que executa o worker; credenciais não são exibidas nem armazenadas no SQLite. `RCLONE=/usr/bin/rclone` define o executável estrutural e `RCLONE_REMOTE=palworld-manager` define o nome validado do remote. O namespace interno é fixo em `Palworld Manager/Backups/` e não é editável no painel.

O Manager trabalha nesse namespace exclusivo para:

- testar conexão e consultar quota;
- enviar backups válidos;
- listar e baixar backups gerenciados;
- baixar e validar um backup remoto para a área local;
- aplicar retenção de até 10 backups próprios.

Antes de um upload, o Manager verificará o armazenamento gratuito disponível. A retenção só poderá remover backups reconhecidos como próprios; se ainda não houver quota, o upload será cancelado e o backup local permanecerá intacto.

Na V1, somente o backup diário automático válido solicita upload automático.
Backups manuais e preventivos de Restore ou Update permanecem locais por padrão.
O painel permite solicitar manualmente o envio de qualquer backup local válido.
O upload é sempre um job posterior e independente: ele só começa quando o
artefato local possui integridade validada, SHA-256 externo e registro válido, e
uma falha no Drive nunca invalida nem remove a cópia local.

> O Palworld Manager nunca pode excluir arquivos externos à sua área de backups.

## Contrato implementado

Somente o worker executa rclone, sempre com lista de argumentos, `shell=False`,
timeouts e prompts desabilitados. O adapter usa `about --json` para quota,
`lsjson` com SHA-256 para metadados, `copyto` para transferências unitárias e
publicação remota por nome temporário seguida de `moveto`. O SHA-256 remoto e o
tamanho devem coincidir com o registro local antes de criar o registro `DRIVE`.
Saída inválida, timeout, autenticação ausente ou indisponibilidade são reduzidos
a categorias seguras, sem stderr, configuração ou credenciais nos registros.

Downloads usam staging em `tmp/drive/`, conferem primeiro o SHA-256 registrado e
depois reutilizam a validação integral do backup local. O resultado é somente
uma cópia local válida; a Etapa 20 não conecta esse download ao Restore.

Retenção e exclusão chamam `deletefile` apenas para um nome previamente
registrado como `DRIVE` e `VALID`, dentro do namespace fixo e compatível com o
padrão do Manager. A exclusão é permanente porque itens na lixeira ainda
consomem quota. Objetos apenas encontrados pela listagem, mesmo que tenham nome
parecido, não são adotados nem removidos. Temporários de upload possuem padrão
separado com job e identificador aleatório; o startup remove somente os que
pertencem a jobs terminais conhecidos.

Development e test usam `FakeGoogleDriveStorage`, sem processo rclone, rede ou
credencial real. O fake cobre quota, listagem, upload, download, exclusão,
cancelamento e falhas controladas.

O download temporário conectado ao fluxo de Restore permanece exclusivo da
Etapa 21. Consulte [backup e restore](../operations/backup-restore.md) e
[SPECIFICATION.md](../../SPECIFICATION.md).
