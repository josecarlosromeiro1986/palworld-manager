# Google Drive com rclone

> Status: Implementado para backup e Restore remotos.

rclone conecta o Manager ao Google Drive. A configuração inicial e a autenticação do remote são feitas manualmente no terminal, sob o mesmo usuário `palmanager` que executa o worker; credenciais não são exibidas nem armazenadas no SQLite. `RCLONE=/usr/bin/rclone` define o executável estrutural e `RCLONE_REMOTE=palworld-manager` define o nome validado do remote. O namespace interno é fixo em `Palworld Manager/Backups/` e não é editável no painel.

## Configuração de produção

### 1. Preparar o OAuth no Google Cloud

Use um projeto dedicado no [Google Cloud Console](https://console.cloud.google.com/)
e uma conta Google que será proprietária dos backups. O `client_id`, o
`client_secret` e os tokens OAuth são credenciais: não os cole em tickets,
logs, documentação, comandos gravados no histórico ou no chat.

1. Em **APIs e serviços > Biblioteca**, habilite **Google Drive API**, conforme
   o [guia oficial de APIs](https://developers.google.com/workspace/guides/enable-apis).
2. Em **Google Auth Platform**, conclua **Branding**, **Audience** e
   **Data Access**. Em Branding, informe nome do aplicativo, e-mail de suporte e
   contato do desenvolvedor; se o console solicitar homepage e política de
   privacidade, publique páginas HTTPS válidas. A política versionada do projeto
   está em [Política de Privacidade](../privacy.md) e pode ser publicada junto
   com `docs/` pelo GitHub Pages ou outro host estático. Essa publicação expõe
   somente a documentação; não publique o Manager nem habilite Tailscale Funnel.
   Em Data Access, declare o
   escopo completo do Google Drive usado pelo remote. Para uma conta Google
   comum, use audiência externa. Enquanto o aplicativo estiver em teste,
   adicione a conta de backup como test user. Consulte a
   [configuração oficial do consentimento](https://developers.google.com/workspace/guides/configure-oauth-consent).
3. Publique o aplicativo em produção antes do uso contínuo. Um aplicativo
   externo deixado no modo de teste pode exigir nova autorização periodicamente.
4. Em **Clients**, crie um cliente OAuth do tipo **Desktop app**. Guarde o
   Client ID e o Client secret em um gerenciador de senhas.

O remote de produção validado usa o escopo `drive`, necessário ao fluxo completo
de upload, listagem, download, verificação e exclusão de backups. O Manager
restringe suas operações ao namespace fixo `Palworld Manager/Backups/`, mas o
consentimento Google continua sendo uma concessão ampla; por isso use uma conta
dedicada sempre que possível.

O uso de um Client ID próprio é obrigatório para uma instalação duradoura: o
client compartilhado do rclone está sendo descontinuado durante 2026. Consulte
o procedimento oficial em [Making your own client_id](https://rclone.org/drive/#making-your-own-client-id).

### 2. Autorizar o remote no servidor sem navegador

A Etapa 29 fixa `RCLONE_CONFIG` em
`/var/lib/palworld-manager/rclone/rclone.conf`. O diretório pertence a
`palmanager:palmanager`, usa modo `0700`, e o arquivo usa modo `0600`.
Somente o worker e comandos administrativos executados como `palmanager`
acessam essa configuração. Ela permanece fora do repositório, do SQLite, do
payload de backup e do journal.

Crie o arquivo protegido antes de abrir o assistente:

```bash
sudo test -e /var/lib/palworld-manager/rclone/rclone.conf || sudo install -o palmanager -g palmanager -m 0600 /dev/null /var/lib/palworld-manager/rclone/rclone.conf
```

Se o servidor não tem navegador, abra em sua estação um túnel SSH para a porta
local usada pelo callback do rclone. Mantenha esse terminal aberto durante a
autorização:

```bash
ssh -L 53682:127.0.0.1:53682 USUARIO_SSH@SERVIDOR
```

Em outro terminal conectado ao servidor, execute:

```bash
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone config
```

Responda ao assistente com estes valores:

| Pergunta | Valor de produção |
| --- | --- |
| criar remote | `n` |
| nome | `palworld-manager` |
| storage | `drive` — Google Drive |
| `client_id` | o Client ID próprio, informado somente no terminal protegido |
| `client_secret` | o Client secret próprio, informado somente no terminal protegido |
| scope | `drive` — acesso completo |
| `service_account_file` | vazio |
| configuração avançada | `n` |
| autenticação pelo navegador | `y` com o túnel ativo |
| Shared Drive | `n`, salvo se a instalação usar deliberadamente um Shared Drive |
| manter remote | `y` |

O servidor pode informar que não conseguiu abrir `xdg-open` e mostrar uma URL
local `http://127.0.0.1:53682/auth?...`. Isso é esperado em um host headless:
abra essa URL no navegador da estação que mantém o túnel, autorize a conta de
backup e aguarde o terminal do rclone continuar. A URL contém estado temporário;
não a compartilhe. Se a URL mostrar outra porta, recrie o túnel com essa porta.
Encerre o assistente com `q` depois de salvar o remote.

### 3. Validar sem revelar credenciais

Nunca use `rclone config show` em evidências ou suporte. Valide somente metadata,
nome do remote e uma chamada autenticada sem imprimir a resposta:

```bash
sudo stat -c '%U %G %a %n' /var/lib/palworld-manager/rclone/rclone.conf
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone listremotes
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone about palworld-manager: --json >/dev/null
```

O resultado deve mostrar `palmanager palmanager 600`, o remote
`palworld-manager:` e exit code zero no `about`. Depois que web e worker
estiverem ativos, use **Configurações do Painel > Testar conexão e quota**. O
job `DRIVE_CHECK` deve terminar como `SUCCEEDED`.

Se o secret OAuth for exposto, revogue o cliente no Google Cloud, crie outro,
refaça o remote e mantenha o arquivo com modo `0600`. Se apenas o consentimento
da conta precisar ser revogado, remova o acesso do aplicativo na Conta Google e
autorize novamente pelo mesmo procedimento.

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

Versões do rclone podem serializar a chave do hash retornado por `lsjson` como
`SHA-256` ou `sha256`. O adapter aceita exatamente essas duas grafias,
normaliza o digest hexadecimal e rejeita metadados ausentes, inválidos ou
conflitantes antes de publicar o backup remoto.

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
