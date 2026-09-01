# Instalação em produção

> Status: Implementado na Etapa 29 e integrado ao deploy recorrente da Etapa 30.
> Este procedimento instala a aplicação pela primeira vez; atualizações
> posteriores usam o [runbook de deploy](deploy.md).

A produção usa Ubuntu Server, Python em ambiente virtual e dois serviços systemd
nativos, sem Docker:

```text
Tailscale Serve (HTTPS privado)
             ↓
127.0.0.1:8080
             ↓
palworld-manager.service       → FastAPI
palworld-manager-worker.service → jobs, heartbeat e integrações externas
             ↓
/var/lib/palworld-manager/manager.db
```

Os comandos abaixo pressupõem estes paths estruturais definidos na
[especificação](../../SPECIFICATION.md):

```text
Código:          /opt/palworld-manager
Dados:           /var/lib/palworld-manager
Configuração:    /etc/palworld-manager/manager.env
Secrets:         /etc/palworld-manager/secrets.env
rclone:          /var/lib/palworld-manager/rclone/rclone.conf
Palworld:        /home/steam/palserver
Serviço Palworld: palworld.service
```

Se a instalação real usa outro path ou outra unidade, altere simultaneamente a
configuração estrutural, o sandbox, o adapter, o helper root e os templates
systemd. Não libere curingas nem uma unidade arbitrária.

## 1. Pré-requisitos

Use Ubuntu Server com systemd e Python 3.12 ou mais recente. Instale as
dependências do runtime, build e validação:

```bash
sudo apt update
sudo apt install --no-install-recommends acl git nodejs npm python3 python3-venv rclone sqlite3 sudo curl
/usr/bin/python3.12 --version
node --version
npm --version
/usr/bin/rclone version
```

No Ubuntu 22.04, o `/usr/bin/python3` da distribuição pode continuar em 3.10.
Instale Python 3.12 ou superior por uma fonte confiável, preserve o interpretador
do sistema operacional e use explicitamente o executável novo para criar a venv.
Os exemplos abaixo assumem `/usr/bin/python3.12`.

Instale e conecte o Tailscale pelo procedimento oficial da distribuição antes de
configurar o Serve. Confirme que os executáveis usados pelo código possuem os
paths esperados:

```bash
test -x /usr/bin/systemctl
test -x /usr/bin/journalctl
test -x /usr/bin/sudo
test -x /usr/bin/tailscale
test -x /usr/bin/rclone
test -x /usr/bin/getfacl
test -x /usr/bin/setfacl
test -x /usr/games/steamcmd
```

O SteamCMD e o serviço `palworld.service` devem estar instalados e funcionais
antes do Manager. Não prossiga se `PALWORLD_DIR`, o mundo ou os INIs forem
symlinks; os adapters de produção os recusam.

## 2. Usuário e grupos

Crie o usuário de sistema sem shell interativo, o grupo compartilhado do
Palworld e a associação de leitura ao journal:

```bash
sudo groupadd --system palworld-manager
sudo useradd --system --user-group --home-dir /var/lib/palworld-manager --create-home --shell /usr/sbin/nologin palmanager
sudo usermod --append --groups palworld-manager,systemd-journal palmanager
id palmanager
```

`palmanager` é o usuário de web e worker. O grupo `palworld-manager` concede
somente o acesso necessário à instalação do Palworld; `systemd-journal` permite
a leitura não-root que o visualizador e as validações pós-operação exigem.

## 3. Checkout, venv e assets

Obtenha um checkout autenticado e confiável em `/opt/palworld-manager`. O
repositório é privado: use SSH ou um credential helper e nunca coloque token na
URL, no histórico ou na configuração Git. Exemplo, substituindo somente a URL:

```bash
sudo git clone REPOSITORY_URL /opt/palworld-manager
cd /opt/palworld-manager
git status --short --branch
git rev-parse HEAD
```

O build roda sem privilégios de root. Durante essa fase, entregue o checkout ao
usuário de serviço; depois do build, devolva o código a `root` e retire escrita
do runtime:

```bash
sudo chown --recursive palmanager:palmanager /opt/palworld-manager
sudo -u palmanager /usr/bin/python3.12 -m venv /opt/palworld-manager/.venv
sudo -u palmanager /opt/palworld-manager/.venv/bin/python -m pip install --upgrade pip
sudo -u palmanager npm ci --prefix /opt/palworld-manager
sudo -u palmanager npm run build --prefix /opt/palworld-manager
sudo -u palmanager /opt/palworld-manager/.venv/bin/python -m pip install /opt/palworld-manager
sudo chown --recursive root:palmanager /opt/palworld-manager
sudo chmod --recursive g-w,o-rwx /opt/palworld-manager
sudo find /opt/palworld-manager -type d -exec chmod g+rx {} +
sudo find /opt/palworld-manager -type f -exec chmod g+r {} +
```

Node/npm são usados somente nessa compilação. Nenhuma unit de produção executa
Node. O pacote Python inclui os templates Jinja2 e os arquivos gerados em
`app/static/dist/`.

## 4. Diretórios e configuração estrutural

Instale a política tmpfiles e crie as áreas administradas:

```bash
cd /opt/palworld-manager
sudo install -o root -g root -m 0644 ops/tmpfiles/palworld-manager.conf /etc/tmpfiles.d/palworld-manager.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/palworld-manager.conf
```

Ela cria somente:

- `/etc/palworld-manager` como `root:palmanager`, modo `0750`;
- dados, backups e jobs como `palmanager:palmanager`, modo `0750`;
- staging e diretório do rclone como `palmanager:palmanager`, modo `0700`.

Instale a configuração estrutural versionada, que não contém secrets:

```bash
sudo install -o root -g palmanager -m 0640 ops/environment/manager.env /etc/palworld-manager/manager.env
```

Crie o arquivo de secrets sem copiar seu conteúdo para o terminal, logs ou
documentação:

```bash
sudo install -o root -g palmanager -m 0640 /dev/null /etc/palworld-manager/secrets.env
sudoedit /etc/palworld-manager/secrets.env
```

O arquivo deve definir `PALWORLD_REST_USERNAME` e
`PALWORLD_REST_PASSWORD`. `DISCORD_WEBHOOK_URL` é opcional. Use uma linha
`NOME=valor` por variável, com quoting compatível com
`systemd.exec(5)`. Não use `export`, não registre valores em tickets e não
execute `cat` nesse arquivo.

Valide apenas metadata e nomes esperados:

```bash
sudo stat -c '%U %G %a %n' /etc/palworld-manager/manager.env /etc/palworld-manager/secrets.env
sudo grep -Eq '^PALWORLD_REST_USERNAME=' /etc/palworld-manager/secrets.env
sudo grep -Eq '^PALWORLD_REST_PASSWORD=' /etc/palworld-manager/secrets.env
```

Os resultados de `stat` devem mostrar `root palmanager 640`. Os `grep`
acima não imprimem valores.

## 5. rclone

Crie a configuração separada dos secrets da aplicação. Ela precisa ser gravável
por `palmanager` porque tokens OAuth podem ser renovados:

```bash
sudo install -o palmanager -g palmanager -m 0600 /dev/null /var/lib/palworld-manager/rclone/rclone.conf
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone config
```

Na interface interativa, crie o remote exato `palworld-manager` para Google
Drive. Não configure outro namespace: o código limita todas as operações a
`Palworld Manager/Backups/`.

Valide sem mostrar a configuração:

```bash
sudo stat -c '%U %G %a %n' /var/lib/palworld-manager/rclone/rclone.conf
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone listremotes
sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone about palworld-manager: --json >/dev/null
```

O arquivo deve permanecer `palmanager:palmanager 600`. Não o inclua em
backups, diagnósticos ou commits.

## 6. Permissões do Palworld

O worker precisa ler o mundo para Backup e escrever em `PALWORLD_DIR` para
Restore e SteamCMD. O editor web precisa substituir atomicamente somente os INIs
permitidos. O grupo compartilhado atende esses fluxos sem executar a aplicação
como root.

Antes de alterar modos, confirme os alvos absolutos e a ausência de symlinks:

```bash
test -d /home/steam/palserver
test ! -L /home/steam/palserver
test -d /home/steam/palserver/Pal/Saved/SaveGames
test ! -L /home/steam/palserver/Pal/Saved/SaveGames
test -f /home/steam/palserver/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini
test ! -L /home/steam/palserver/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini
```

O ancestral `/home/steam` pode usar `0750` para proteger a conta Steam e, nesse
caso, bloquear a travessia de `palmanager` antes de `PALWORLD_DIR`. Preserve o
owner, grupo e modo existentes da home e conceda ao grupo compartilhado somente
`--x` por ACL POSIX. Não adicione `palmanager` ao grupo `steam`, não aplique
`o+x` e não crie ACL recursiva ou default:

```bash
test -d /home/steam
test ! -L /home/steam
sudo setfacl --modify group:palworld-manager:--x /home/steam
sudo getfacl --absolute-names /home/steam | grep -Fx 'group:palworld-manager:--x'
```

Aplique o grupo somente na árvore estrutural definida:

```bash
sudo chgrp --recursive palworld-manager /home/steam/palserver
sudo chmod --recursive g+rwX,o-rwx /home/steam/palserver
sudo find /home/steam/palserver -type d -exec chmod g+s {} +
```

Instale o drop-in que mantém o grupo e o `umask` nos arquivos novos do
Palworld:

```bash
sudo install -d -o root -g root -m 0755 /etc/systemd/system/palworld.service.d
sudo install -o root -g root -m 0644 ops/systemd/palworld.service.d/10-palworld-manager-access.conf /etc/systemd/system/palworld.service.d/10-palworld-manager-access.conf
```

O drop-in não muda `ExecStart`, usuário ou comando do Palworld. Ele adiciona
somente `SupplementaryGroups=palworld-manager` e `UMask=0007`. Recarregue o
systemd e reinicie o Palworld em uma janela segura para que o processo existente
receba o grupo suplementar:

```bash
sudo systemctl daemon-reload
sudo systemctl restart palworld.service
```

Não faça esse restart com jogadores conectados ou operação ambígua. Use o
procedimento operacional de manutenção já adotado para o servidor.

Valide como o usuário não-root:

```bash
sudo -u palmanager test -r /home/steam/palserver/Pal/Saved/SaveGames
sudo -u palmanager test -w /home/steam/palserver
sudo -u palmanager test -w /home/steam/palserver/Pal/Saved/Config/LinuxServer
sudo -u palmanager test -x /usr/games/steamcmd
```

## 7. Migrations e administrador inicial

Aplique migrations antes de iniciar os serviços. O transient service lê os dois
arquivos protegidos sem colocar valores secretos na linha de comando:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --unit=palworld-manager-migrate \
  --property=Type=oneshot \
  --property=User=palmanager \
  --property=Group=palmanager \
  --property=UMask=0027 \
  --property=WorkingDirectory=/opt/palworld-manager \
  --property=EnvironmentFile=/etc/palworld-manager/manager.env \
  --property=EnvironmentFile=/etc/palworld-manager/secrets.env \
  /opt/palworld-manager/.venv/bin/alembic upgrade head
```

Valide o banco sem consultar dados da aplicação:

```bash
sudo test -f /var/lib/palworld-manager/manager.db
sudo test ! -L /var/lib/palworld-manager/manager.db
test "$(sudo stat -c '%U:%G:%a' /var/lib/palworld-manager/manager.db)" = 'palmanager:palmanager:640'
sudo -u palmanager sqlite3 -readonly /var/lib/palworld-manager/manager.db \
  'PRAGMA integrity_check; SELECT version_num FROM alembic_version;'
```

O primeiro resultado do SQLite deve ser `ok`; a revisão deve coincidir com o
`head` do checkout. Não edite `alembic_version` manualmente.

Crie o administrador de forma interativa, sem senha em argumentos:

```bash
sudo systemd-run --quiet --wait --pty --collect \
  --unit=palworld-manager-admin-create \
  --property=Type=oneshot \
  --property=User=palmanager \
  --property=Group=palmanager \
  --property=UMask=0027 \
  --property=WorkingDirectory=/opt/palworld-manager \
  --property=EnvironmentFile=/etc/palworld-manager/manager.env \
  --property=EnvironmentFile=/etc/palworld-manager/secrets.env \
  /opt/palworld-manager/.venv/bin/python -m app.cli create-admin
```

A senha é solicitada com entrada oculta. Para redefinição futura, troque apenas
`create-admin` por `reset-password`.

## 8. Gatilhos systemd.path, helper e units

Valide os artefatos versionados antes de instalá-los. Isso verifica sintaxe e
estrutura sem executar lifecycle, sinal ou energia do host:

```bash
cd /opt/palworld-manager
bash -n ops/scripts/palworld-manager-host-control
sudo systemd-analyze verify ops/systemd/palworld-manager.service ops/systemd/palworld-manager-worker.service
sudo install -o root -g root -m 0750 ops/scripts/palworld-manager-host-control /usr/local/sbin/palworld-manager-host-control
sudo install -o root -g root -m 0644 ops/systemd/palworld-manager.service /etc/systemd/system/palworld-manager.service
sudo install -o root -g root -m 0644 ops/systemd/palworld-manager-worker.service /etc/systemd/system/palworld-manager-worker.service
sudo install -o root -g root -m 0644 ops/systemd/palworld-manager-host-control@.service /etc/systemd/system/palworld-manager-host-control@.service
sudo install -o root -g root -m 0644 ops/systemd/palworld-manager-host-control@.path /etc/systemd/system/palworld-manager-host-control@.path
sudo rm -f -- /etc/polkit-1/rules.d/50-palworld-manager-host-control.rules /etc/sudoers.d/palworld-manager
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/palworld-manager.service /etc/systemd/system/palworld-manager-worker.service /etc/systemd/system/palworld-manager-host-control@.service /etc/systemd/system/palworld-manager-host-control@.path
sudo install -o root -g root -m 0750 deploy.sh /usr/local/sbin/palworld-manager-deploy
sudo systemctl enable --now palworld-manager-host-control@palworld-start.path
sudo systemctl enable --now palworld-manager-host-control@palworld-stop.path
sudo systemctl enable --now palworld-manager-host-control@palworld-restart.path
sudo systemctl enable --now palworld-manager-host-control@palworld-sigterm.path
sudo systemctl enable --now palworld-manager-host-control@palworld-sigkill.path
sudo systemctl enable --now palworld-manager-host-control@host-reboot.path
sudo systemctl enable --now palworld-manager-host-control@host-poweroff.path
sudo systemctl enable --now palworld-manager.service
sudo systemctl enable --now palworld-manager-worker.service
```

Web e worker usam `User=palmanager`, `Group=palmanager`, `UMask=0027`,
`NoNewPrivileges=true`, `RestrictSUIDSGID=true`, `ProtectSystem=strict`,
diretórios graváveis explícitos e journald. O template `oneshot` privilegiado
não é habilitado diretamente. Sete instâncias `systemd.path` exatas observam os
pedidos vazios em `/run/palworld-manager/host-control`, acionam o `oneshot` root
correspondente e o helper traduz cada instância para um único comando fixo.
Não há grant Polkit ou sudo para a aplicação.

Somente o worker usa `SystemCallArchitectures=native x86` e
`MemoryDenyWriteExecute=false`, necessários ao bootstrap de 32 bits e ao
runtime oficial do SteamCMD. Web e helper privilegiado mantêm arquitetura nativa
e `MemoryDenyWriteExecute=true`; as demais proteções do worker não são
relaxadas.

A primeira verificação omite deliberadamente os templates privilegiados porque o
`systemd-analyze` exige que o alvo absoluto de `ExecStart` já exista. Depois que
o helper protegido é instalado, a segunda verificação inclui as quatro units e
ocorre antes de iniciar web ou worker.

## 9. Tailscale Serve

Com a web já saudável em loopback, publique somente para a tailnet:

```bash
sudo /usr/bin/tailscale serve --bg 127.0.0.1:8080
/usr/bin/tailscale status --json >/dev/null
/usr/bin/tailscale serve status --json
```

O modo `--bg` persiste a configuração após reinício do daemon. Não habilite
Tailscale Funnel. A aplicação mantém autenticação própria, enquanto dispositivos
e identidade de rede permanecem sob controle do Tailscale.

## 10. Validação final separada

### Web

```bash
systemctl is-active --quiet palworld-manager.service
test "$(systemctl show --property=User --value palworld-manager.service)" = palmanager
curl --fail --silent --show-error http://127.0.0.1:8080/health
curl --silent --dump-header - --output /dev/null http://127.0.0.1:8080/login | grep -Ei '^(content-security-policy|cache-control|x-content-type-options|x-frame-options|strict-transport-security):'
ss -ltn '( sport = :8080 )'
```

O retorno HTTP deve ser mínimo, os headers de hardening devem aparecer e o
listener deve existir somente em `127.0.0.1:8080`, nunca em `0.0.0.0` ou
`[::]`. Em production, HSTS é esperado porque o acesso do navegador ocorre pelo
HTTPS privado do Tailscale Serve.

### Worker

```bash
systemctl is-active --quiet palworld-manager-worker.service
test "$(systemctl show --property=User --value palworld-manager-worker.service)" = palmanager
sudo -u palmanager sqlite3 -readonly /var/lib/palworld-manager/manager.db "SELECT COALESCE((SELECT CASE WHEN (julianday('now') - julianday(heartbeat_at)) * 86400.0 < 30.0 THEN 'HEALTHY' ELSE 'UNRESPONSIVE' END FROM worker_heartbeats WHERE key = 'PRIMARY'), 'MISSING');"
```

Execute a consulta logo após o `systemctl`. O resultado esperado é
`HEALTHY`. `MISSING` durante os primeiros 30 segundos corresponde ao período
`STARTING`; depois disso ou com timestamp antigo, investigue
`UNRESPONSIVE`. O worker não publica porta ou endpoint HTTP.

### Permissões, journal e fronteira privilegiada

```bash
sudo -u palmanager test -r /var/lib/palworld-manager/manager.db
sudo -u palmanager test -w /var/lib/palworld-manager/manager.db
sudo -u palmanager test ! -w /opt/palworld-manager/pyproject.toml
sudo -u palmanager /usr/bin/journalctl --unit palworld.service --output json --output-fields MESSAGE,PRIORITY --lines 1 --no-pager --quiet >/dev/null
sudo test ! -e /etc/polkit-1/rules.d/50-palworld-manager-host-control.rules
sudo test ! -e /etc/sudoers.d/palworld-manager
sudo stat -c '%U %G %a %n' /etc/palworld-manager/secrets.env /var/lib/palworld-manager/rclone/rclone.conf /run/palworld-manager/host-control /usr/local/sbin/palworld-manager-host-control /etc/systemd/system/palworld-manager-host-control@.service /etc/systemd/system/palworld-manager-host-control@.path
for action in palworld-start palworld-stop palworld-restart palworld-sigterm palworld-sigkill host-reboot host-poweroff; do systemctl is-enabled --quiet "palworld-manager-host-control@${action}.path" && systemctl is-active --quiet "palworld-manager-host-control@${action}.path" || exit 1; done
systemctl show palworld-manager-worker.service --property=NoNewPrivileges --property=RestrictSUIDSGID --property=MemoryDenyWriteExecute --property=SystemCallArchitectures
```

No worker, são esperados `NoNewPrivileges=yes`,
`RestrictSUIDSGID=yes`, `MemoryDenyWriteExecute=no` e
`SystemCallArchitectures=native x86`. Não teste reboot, poweroff, sinais ou
comandos de lifecycle apenas para validar
a autorização. `bash -n`, `systemd-analyze verify`, metadados e
as propriedades efetivas do worker são verificações sem efeito no host.

### Journald e acesso privado

```bash
journalctl --unit palworld-manager.service --unit palworld-manager-worker.service --since today --no-pager
/usr/bin/tailscale serve status --json
```

Revise somente categorias e mensagens controladas; não copie ambientes ou
arquivos de secrets para o journal. De outro dispositivo autorizado da tailnet,
abra a URL HTTPS exibida pelo Serve e confirme login e logout.

## 11. Falhas e limites desta etapa

- Não use Docker em produção.
- Não execute web ou worker como root.
- Não conceda escrita fora do diretório fechado de pedidos nem use Polkit ou
  sudo para a aplicação, SteamCMD ou rclone.
- Não exponha a porta 8080 na LAN ou Internet.
- Não habilite Funnel.
- Não copie `secrets.env` ou `rclone.conf` para backups, logs ou repositório.
- Não retome Restore ou Update interrompido sem verificar o estado real.
- Não use este procedimento como atualização recorrente.

A atualização recorrente, as validações pós-restart e o rollback manual estão no
[runbook de deploy](deploy.md). Consulte também
[Segurança](../architecture/security.md), [Jobs e locks](../architecture/jobs-and-locks.md),
[Backup e restore](backup-restore.md), [Energia do host](host-power.md),
[Tailscale](../integrations/tailscale.md) e
[Google Drive com rclone](../integrations/google-drive-rclone.md).
