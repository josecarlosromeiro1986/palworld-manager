# Google Drive com rclone

> Status: Implementado para backup e Restore remotos.

rclone conecta o Manager ao Google Drive. A configuração inicial e a autenticação do remote são feitas manualmente no terminal, sob o mesmo usuário `palmanager` que executa o worker; credenciais não são exibidas nem armazenadas no SQLite. `RCLONE=/usr/bin/rclone` define o executável estrutural e `RCLONE_REMOTE=palworld-manager` define o nome validado do remote. O namespace interno é fixo em `Palworld Manager/Backups/` e não é editável no painel.

## Configuração de produção

A Etapa 29 fixa `RCLONE_CONFIG` em
`/var/lib/palworld-manager/rclone/rclone.conf`. O diretório pertence a
`palmanager:palmanager`, usa modo `0700`, e o arquivo usa modo `0600`.
Somente o worker e comandos administrativos executados como `palmanager`
acessam essa configuração. Ela permanece fora do repositório, do SQLite, do
payload de backup e do journal.

```bash
sudo install -o palmanager -g palmanager -m 0600 /dev/null /var/lib/palworld-manager/rclone/rclone.conf
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone config
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone about palworld-manager: --json >/dev/null
```

Tokens OAuth podem ser renovados, portanto o arquivo precisa continuar gravável
pelo worker; não o mova para o arquivo estrutural de secrets somente leitura. O
procedimento completo está no
[runbook de produção](../operations/production-install.md).

No startup do adapter real, o Manager recusa executável ou configuração
ausente, não regular ou atravessada por symlink. O `rclone.conf` precisa
pertencer ao usuário efetivo e não pode permitir qualquer acesso de grupo ou
outros. O subprocesso recebe um ambiente mínimo e o único valor específico é o
`RCLONE_CONFIG` validado; secrets REST e Discord nunca são herdados.

O Manager trabalha nesse namespace exclusivo para:

- testar conexão e consultar quota;
- enviar backups válidos;
- listar e baixar backups gerenciados;
- baixar e validar um backup remoto para a área local;
- aplicar a retenção configurada, inicialmente 10 e editável entre 1 e 100 backups próprios.

Antes de um upload, o Manager verifica o armazenamento gratuito disponível. A retenção só remove backups reconhecidos como próprios; se ainda não houver quota, o upload é cancelado e o backup local permanece intacto.

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

Downloads solicitados como cópia local usam staging em `tmp/drive/`, conferem
primeiro o SHA-256 registrado e depois reutilizam a validação integral do backup
local. Somente esse fluxo publica uma nova cópia `LOCAL` válida.

O Restore remoto usa um job `REMOTE_RESTORE` separado dos jobs comuns de
download. Ele baixa o mesmo artefato para staging controlado e, sem publicar uma
cópia local, entrega o arquivo validado ao pipeline completo de Restore. O
SHA-256 externo, tar.gz, manifest, hashes individuais, payload e configurações
combinadas são conferidos antes do backup preventivo e do Stop. Ao fim da
preparação ou diante de falha conhecida, somente o staging do job é removido; o
registro e o objeto remoto não são alterados.

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

O botão de teste em **Configurações do Painel** apenas cria um job persistente
`DRIVE_CHECK` e acompanha seu resultado seguro. Somente o worker executa a
consulta de conexão e quota; um double-submit não cria outro job enquanto já
existe uma operação Drive incompatível ativa. Remote, executável, namespace e
credenciais não são exibidos nem editados nessa página.

Consulte [backup e restore](../operations/backup-restore.md) e
[SPECIFICATION.md](../../SPECIFICATION.md).
