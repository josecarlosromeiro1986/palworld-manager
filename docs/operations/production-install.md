# Instalação em produção

> Status: Planejado para a V1.

A produção será uma instalação nativa em Ubuntu Server, sem Docker:

```text
Ubuntu Server
+ venv
+ systemd
+ Tailscale Serve
```

Identificadores planejados:

```text
Usuário:         palmanager
Aplicação:       /opt/palworld-manager
Banco SQLite:    /var/lib/palworld-manager/manager.db
Serviço web:     palworld-manager.service
Serviço worker:  palworld-manager-worker.service
Secrets:         /etc/palworld-manager/secrets.env
```

A configuração estrutural inclui `PALWORLD_REST_BASE_URL=http://127.0.0.1:8212/v1/api`. O arquivo protegido de secrets deve fornecer `PALWORLD_REST_USERNAME` e `PALWORLD_REST_PASSWORD`; ambos são obrigatórios, não têm valor padrão e não usam fallback para `admin`. Web e worker falham no startup com erro de configuração quando esses valores estão ausentes, vazios ou inválidos.

A integração remota usa `RCLONE=/usr/bin/rclone` e
`RCLONE_REMOTE=palworld-manager`. A autenticação inicial do remote é manual no
terminal sob `palmanager`; o arquivo de configuração do rclone deve permanecer
restrito a esse usuário e nunca é copiado para SQLite, logs ou backups. O
procedimento completo de instalação e validação será consolidado na Etapa 29.

O webhook opcional do Discord usa `DISCORD_WEBHOOK_URL` no mesmo arquivo
protegido de secrets. Quando configurado, deve ser uma URL HTTPS oficial do
Discord, sem query, fragmento ou credenciais adicionais. Somente o worker utiliza
esse valor; ele nunca é persistido no SQLite, exibido na UI ou reproduzido em
logs e auditoria.

Os serviços web e worker serão processos independentes configurados via systemd. Ambos serão executados pelo usuário `palmanager`, nunca como `root`, usarão a mesma configuração estrutural apropriada e acessarão o mesmo banco SQLite quando necessário.

`palworld-manager.service` executará o FastAPI e escutará somente em `127.0.0.1`. Seu `/health` verificará exclusivamente a aplicação web. `palworld-manager-worker.service` consumirá os jobs persistidos, executará as operações demoradas ou críticas e será o único processo autorizado a entregar notificações externas.

O worker não tem servidor HTTP. Ele atualiza um heartbeat no SQLite a cada 10 segundos; sua saúde combina o heartbeat com o estado e o tempo de ativação do serviço no systemd. Serviço ativo sem heartbeat fica `STARTING` com menos de 30 segundos desde a ativação e `UNRESPONSIVE` a partir de 30 segundos. Heartbeat inferior a 30 segundos com serviço ativo significa `HEALTHY`, heartbeat de 30 segundos ou mais significa `UNRESPONSIVE`, e serviço inativo significa `OFFLINE`. O heartbeat funciona também como lease: uma identidade concorrente é recusada enquanto o sinal anterior tiver menos de 30 segundos.

Logs textuais de jobs usam `/var/lib/palworld-manager/jobs/<ano>/`, guardam apenas mensagens operacionais controladas e têm retenção de 90 dias. O SQLite mantém somente a referência relativa. A etapa de deploy deverá criar e proteger essa árvore para `palmanager`; a aplicação não requer acesso fora de `/var/lib/palworld-manager` para esses logs.

Backups locais usam `/var/lib/palworld-manager/backups/` e staging em `/var/lib/palworld-manager/tmp/backups/`; os arquivos finais pertencem a `palmanager` e usam modo `0640`. O backup exige leitura no subtree estrutural `PALWORLD_DIR/Pal/Saved/SaveGames` e nas configurações permitidas. O Restore exige também criar e renomear diretórios dentro de `Pal/Saved`, substituir os INIs permitidos e atribuir aos novos arquivos o grupo do mundo anterior, com `0770` em diretórios e `0660` em arquivos. A Etapa 29 deverá colocar `palmanager` somente no grupo compartilhado necessário e conceder essas permissões mínimas, sem executar web ou worker como root e sem `sudo ALL`.

Tailscale Serve fornecerá acesso privado com HTTPS apenas ao serviço web; journald receberá os logs de ambos.

O visualizador de logs do Palworld já usa `journalctl` somente leitura, sem `sudo`, com argumentos fixos para `PALWORLD_SERVICE`. A etapa de deploy deverá conceder ao usuário `palmanager` apenas o acesso de leitura necessário ao journal e validar esse acesso sem executar a aplicação como root.

O editor do `PalWorldSettings.ini` já usa o caminho fixo de `PALWORLD_SETTINGS`, recusa symlinks e cria no mesmo diretório uma cópia pré-save protegida por modo `0600` antes da substituição atômica. A etapa de deploy deverá configurar o menor conjunto de permissões que permita ao usuário `palmanager` ler o INI e criar/substituir arquivos somente nesse diretório, preservando o acesso necessário ao processo do Palworld. O procedimento definitivo ainda não está implementado e não deve ser substituído por `sudo ALL` ou pela execução da web como root.

O health check do Palworld usa adapters com executáveis e argumentos fixos, unidade validada, `MainPID` confirmado por `psutil` e `GET /info` autenticado com timeout. Start, Stop e Restart já são executados pelo worker com `/usr/bin/sudo --non-interactive`, `systemctl --no-block`, ação fechada e unidade validada. A escalada manual usa somente `systemctl kill --kill-whom=main` com SIGTERM ou SIGKILL fixos. Reboot e shutdown do Ubuntu também pertencem exclusivamente ao worker e usam somente `systemctl --no-block reboot` ou `poweroff`, depois do tratamento seguro do Palworld. A etapa de deploy ainda deve instalar e validar regras de sudoers exclusivas para esses sete comandos, nunca `sudo ALL`.

Updates usam o executável estrutural `STEAMCMD` diretamente pelo worker não-root,
com login anônimo e App ID fixo `2394010`. O deploy deverá validar que o binário é
regular e executável por `palmanager` e conceder, por grupo dedicado, somente a
leitura do manifesto `PALWORLD_DIR/steamapps/appmanifest_2394010.acf` e a
leitura/escrita necessárias para o SteamCMD atualizar o conteúdo dentro de
`PALWORLD_DIR`. Não será criada regra de sudoers para SteamCMD, e nenhum acesso de
escrita fora desse diretório será concedido por causa do Update.

Node.js e npm serão necessários apenas para o build de assets, não como serviço de produção. Permissões, arquivos de unidade do Manager, `sudoers`, scripts e configuração do Tailscale ainda serão implementados e validados na etapa de deploy; por isso, este documento não é um tutorial executável.

Consulte [Segurança](../architecture/security.md) e os requisitos completos em [SPECIFICATION.md](../../SPECIFICATION.md).
