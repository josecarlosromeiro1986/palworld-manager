# Segurança

> Status: Implementado para a V1 `1.0.0`, incluindo a revisão final de permissões, secrets, HTTP, sessão, subprocessos, paths, tar, concorrência, logs, retenção e timeouts.

A aplicação segue o princípio do menor privilégio. Em produção, web e worker são
executados pelo usuário Linux dedicado `palmanager`, nunca como `root`. A
fronteira privilegiada usa sete gatilhos `systemd.path`, units `oneshot` root
sem daemon persistente e um helper de ações fechadas. Não há grant Polkit nem
sudo para a aplicação.

## Acesso e autenticação

- FastAPI escuta apenas em localhost e é publicado por Tailscale Serve com HTTPS.
- Tailscale controla os dispositivos autorizados, sem whitelist duplicada no Manager.
- O Manager armazena somente hashes Argon2id. A senha aceita de 6 a 1024 caracteres; o limite superior impede trabalho Argon2 desproporcional e a senha nunca é aceita como argumento de linha de comando.
- A criação do administrador inicial e a redefinição de senha estão disponíveis por CLI interativa, com entrada oculta e confirmação.
- As sessões são server-side no SQLite. Tokens de sessão e CSRF são aleatórios; somente seus hashes ficam no banco.
- O cookie contém um identificador opaco. Sessões duram no máximo 8 horas e expiram após 1 hora de inatividade; logout e troca de senha as revogam.
- Cookies de autenticação usam `HttpOnly` e `SameSite=Strict`. `Secure` é obrigatório em produção e omitido somente em development/test para permitir HTTP local.
- Cinco tentativas inválidas consecutivas para o mesmo usuário causam bloqueio por 15 minutos. Login bem-sucedido ou expiração do bloqueio reinicia a contagem.
- O endereço de origem observado é armazenado para auditoria, mas não compõe a chave do bloqueio. Tentativas e bloqueios são auditados sem registrar senhas.
- A troca de senha pelo painel exige a senha atual, a nova senha e sua confirmação exata. A validação da senha atual reutiliza a proteção contra tentativas abusivas do login; uma troca bem-sucedida grava somente o novo hash Argon2id, revoga inclusive a sessão atual, remove os cookies de autenticação, registra a ação sem valores sensíveis e retorna o administrador ao login.
- Login, logout, controles do servidor, anúncios e gravação do `PalWorldSettings.ini` já validam CSRF. O anúncio também exige que a confirmação repita literalmente o texto livre que será enviado.
- Requisições mutáveis têm corpo limitado a 1 MiB mesmo sem `Content-Length`; valores ausentes são medidos durante a leitura e headers duplicados ou inválidos são recusados.
- Todas as respostas HTTP recebem CSP sem JavaScript inline, bloqueio de framing e MIME sniffing, política de referência e permissões restritas. Respostas dinâmicas usam `Cache-Control: no-store`; production também envia HSTS.

## Secrets e registros

Secrets ficam fora do SQLite, em arquivo de ambiente com acesso restrito em produção. Senhas, tokens, webhooks, cookies, credenciais e chaves de API não podem aparecer completos na interface, em logs, auditorias, fixtures, diagnósticos ou backups gerenciados/exportados. A cópia técnica pré-save do INI é local, exata e restrita a modo `0600`: ela pode conter os valores que já existiam no arquivo porque precisa permitir recuperação fiel, mas não é exibida, persistida no SQLite nem transferida. Logs mascaram nomes sensíveis e evitam registrar headers ou ambientes indiscriminadamente.

O Discord usa exclusivamente `DISCORD_WEBHOOK_URL` como secret estrutural. Em
production, URLs configuradas são limitadas ao endpoint HTTPS oficial, sem
query, fragmento ou credenciais adicionais. Somente o worker usa o secret e
executa o POST; a web apenas persiste eventos. Mensagens usam textos allowlisted
e menções desabilitadas, enquanto falhas persistem apenas categorias seguras.
Development e test usam fake em memória, sem rede ou webhook real.

A configuração estrutural é validada com Pydantic Settings no startup de web e worker. Erros de validação ocultam os valores recebidos, e o ambiente `production` rejeita `APP_HOST` que não seja loopback. `PALWORLD_REST_USERNAME` e `PALWORLD_REST_PASSWORD` são secrets obrigatórios em production; não existe username padrão nem fallback para `admin`. Eles são fornecidos por variáveis de processo provenientes do arquivo protegido de produção.

## Comandos, caminhos e arquivos

- Todas as chamadas de processo usam argumentos separados, `shell=False` e um ambiente allowlisted. Credenciais REST, webhook e demais variáveis da aplicação nunca são herdadas por systemctl, journalctl, SteamCMD ou rclone; este último recebe somente o `RCLONE_CONFIG` estrutural além do ambiente mínimo.
- Aceitar somente comandos, serviços e caminhos previamente permitidos.
- A consulta implementada do Palworld usa o executável fixo `/usr/bin/systemctl`, aceita somente `palworld.service` em production e aplica timeout. Development e test usam um fake e nunca chamam o systemd do host.
- Start, Stop e Restart são executados somente pelo worker, que cria de forma exclusiva um arquivo vazio `palworld-{start,stop,restart}.request` no diretório protegido de runtime. O adapter usa enum fechada, abre o diretório sem seguir symlink, valida owner/grupo/modo e nunca chama o systemd diretamente.
- SIGTERM e SIGKILL criam os pedidos fechados `palworld-sigterm.request` e `palworld-sigkill.request`; o helper root traduz cada um para `systemctl kill --kill-whom=main` com sinal e unidade fixos. SIGTERM exige `FORCAR` após falha real do Stop; SIGKILL exige falha do SIGTERM e a confirmação `SIGKILL`. Não existe escalada automática.
- Reboot e shutdown do Ubuntu são solicitados somente pelo worker, após Stop seguro do Palworld, pelos pedidos fechados `host-reboot.request` e `host-poweroff.request`. A enum não aceita ação ou argumento da requisição e development/test usam fake.
- A verificação do processo consulta somente o `MainPID` da mesma unidade validada. O cliente REST usa URL estrutural validada, timeout, limites de resposta e Basic Auth; credenciais não são incluídas na URL, interface, auditoria ou mensagens de erro. Autenticação rejeitada, timeout, servidor offline, API indisponível, resposta inválida e falha inesperada recebem classificações seguras.
- A leitura de logs usa `/usr/bin/journalctl` sem `sudo`, somente para a unidade validada, com limites fechados, campos mínimos, argumentos separados e `shell=False`. Cursores de reconexão são validados, stderr não é exibido e valores sensíveis conhecidos são mascarados antes do SSE.
- O editor do INI usa somente o caminho estrutural `PALWORLD_SETTINGS`, rejeita arquivos não regulares e qualquer componente symlink, limita a leitura a 1 MiB e detecta alterações concorrentes por SHA-256. A cópia pré-save é criada com modo `0600`; a gravação usa arquivo temporário no mesmo diretório e substituição atômica. Senhas presentes no INI são preservadas, mas nunca exibidas ou incluídas na auditoria.
- Normalizar e validar caminhos contra path traversal e acesso por symlink.
- Ao criar ou extrair `.tar.gz`, caminhos absolutos, `..`, links perigosos e conteúdo fora do destino autorizado são rejeitados.
- A inspeção do tar exige `manifest.json` como primeira entrada e limita o manifest a 4 MiB, cada path a 4096 bytes, o arquivo a 100.000 entradas e o payload declarado a 128 GiB. Os limites são aplicados durante a leitura dos headers, antes de avançar sobre payload excessivo.
- Formato, tamanho e integridade são validados antes de usar um backup.
- O backup local implementado lê somente o subtree estrutural de saves, recusa symlinks e entradas não regulares, exclui backups internos e `secrets.env`, redige campos sensíveis dos INIs e exporta apenas configurações operacionais allowlisted do Manager. O SQLite é copiado pelo mecanismo de backup ativo e validado com `integrity_check`.
- Temporários e arquivos finais de backup ficam sob a área de dados do Manager. A publicação é atômica, o arquivo final usa modo `0640` e cleanup/retenção aceitam somente paths relativos e nomes reconhecidos; arquivos externos são preservados.
- O adapter do Google Drive é usado somente pelo worker. Em production ele exige rclone regular e executável e `RCLONE_CONFIG` regular, sem symlink, pertencente ao próprio processo e sem acesso de grupo/outros. Executa argumentos separados, prompts desabilitados, timeout e `shell=False`; stderr e configuração nunca chegam à UI, aos logs ou à auditoria. O remote tem nome estrutural validado e todas as operações ficam no namespace fixo `Palworld Manager/Backups/`.
- Uploads usam nome temporário reconhecível e só publicam após conferir tamanho e SHA-256 remoto. Download fica em staging controlado e só entra na área local depois da validação completa. Retenção e exclusão permanente aceitam exclusivamente registros `DRIVE`/`VALID` com nome gerenciado; listagens não concedem propriedade sobre arquivos externos.
- O Restore pelo painel valida o artefato completo, mas aplica somente o mundo e as configurações do Palworld. O subtree `manager/` é reservado à recuperação manual/offline de desastre: o SQLite ativo e as configurações atuais do Manager nunca são substituídos por esse fluxo, e web/worker não são parados para restaurar o próprio Manager.
- Antes do Stop, o Restore combina o `PalWorldSettings.ini` sanitizado com o arquivo atual validado: somente campos não sensíveis vêm do backup, valores sensíveis e parâmetros desconhecidos são preservados sem defaults inventados. Ausência, ilegibilidade, invalidade ou merge não determinístico falham sem tocar no mundo e são auditados sem conteúdo ou valores.
- O Restore local usa staging controlado, valida o hash externo antes do manifest, verifica antecipadamente espaço tanto no staging quanto no destino, não usa extração genérica e aceita apenas arquivos regulares em `world/`, `config/` e `manager/`. A publicação do mundo mantém o grupo estrutural anterior, aplica `0770/0660` e é executada pelo worker não-root; symlinks, escape, estado temporário ambíguo e espaço insuficiente são recusados.
- O Restore remoto aceita somente um registro `DRIVE`/`VALID` gerenciado e baixa com rclone para `tmp/drive/`. O staging recusa escape e symlink, tamanho e SHA-256 são conferidos antes da abertura do tar.gz e a validação completa ocorre antes do backup preventivo e do Stop. A tentativa nunca remove ou invalida o objeto remoto e não publica uma cópia local implícita.
- O Update é executado somente pelo worker. Em production, o adapter valida `STEAMCMD` como executável regular absoluto e `PALWORLD_DIR` como diretório estrutural regular, recusa symlinks e usa exclusivamente argumentos separados e fixos para login anônimo, App ID `2394010`, consulta da branch pública e `app_update 2394010 validate`. Não há input livre, credencial Steam, `shell=True`, saída bruta persistida ou execução como `root`. Development e test usam fakes integrais e não executam SteamCMD nem acessam a instalação estrutural do Palworld.
- O usuário `palmanager` receberá no deploy apenas execução do binário SteamCMD e acesso de leitura/escrita necessário ao diretório do Palworld por grupo dedicado. O Update não usa `sudo` para ampliar acesso ao filesystem e não altera binários fora de `PALWORLD_DIR`; Start/Stop continuam restritos às regras fechadas de systemd já definidas.

Operações destrutivas exigem confirmações explícitas, locks e auditoria. Uma operação interrompida não é retomada automaticamente. Os requisitos completos estão em [SPECIFICATION.md](../../SPECIFICATION.md), especialmente nas seções de autenticação, jobs, backup e hardening.

## Baseline de produção

Os artefatos em `ops/` materializam o menor privilégio da instalação:

- web e worker usam `User=palmanager`, `Group=palmanager` e units separadas;
- `UMask=0027`, `ProtectSystem=strict`, capabilities vazias e paths graváveis
  explícitos limitam o filesystem;
- web e worker usam `NoNewPrivileges=true` e `RestrictSUIDSGID=true`; a web
  só grava dados do Manager e o diretório dos INIs, enquanto o worker grava
  dados do Manager e `PALWORLD_DIR`;
- o worker não eleva o próprio processo: seu sandbox permite escrita apenas no
  diretório `root:palmanager 0770` de pedidos, onde cria arquivos vazios,
  exclusivos e `0600`; sete instâncias `systemd.path` exatas acionam os
  `oneshot`, e o helper root não aceita serviço ou argumento livre;
- o grupo compartilhado `palworld-manager` e o drop-in de
  `palworld.service` preservam modos `0770/0660` sem root; quando uma home
  ancestral restrita bloqueia `PALWORLD_DIR`, uma ACL POSIX não recursiva
  concede somente travessia (`--x`) ao grupo compartilhado, sem incluir
  `palmanager` no grupo da conta Steam ou liberar acesso para outros usuários;
- as units persistentes e transitórias executadas como `palmanager` usam
  `UMask=0027`, mantendo `manager.db` como `palmanager:palmanager 0640`;
- `systemd-journal` fornece leitura não-root, enquanto o adapter restringe a
  consulta à unit validada;
- `secrets.env` é `root:palmanager 0640` e o rclone usa configuração separada
  `palmanager:palmanager 0600`;
- a web permanece em loopback e somente Tailscale Serve publica HTTPS privado.

O diretório de pedidos e os sete paths são a única passagem do worker para o
helper. O helper remove o pedido validado antes de executar um dos sete branches
fixos; não existe Polkit, sudo, curinga, SteamCMD ou rclone privilegiado.
Consulte o [ADR-0001](../decisions/0001-systemd-path-host-control.md) e o
[runbook de produção](../operations/production-install.md).

## Deploy recorrente

O `deploy.sh` é um orquestrador administrativo root porque altera o checkout
root-owned, arquivos em `/etc` e serviços. Ele não executa a aplicação como
root: dependências, build, gate, configuração, migrations, web e worker usam
`palmanager`. Paths, serviços, ref remoto e comandos são fixos; o checkout deve
estar limpo e um `flock` impede deploys concorrentes.

A venv de produção e seu diretório de executáveis precisam permanecer
root-owned e sem escrita por grupo/outros. O symlink padrão
`.venv/bin/python` é permitido somente quando resolve para um executável
regular igualmente root-owned e protegido contra escrita; links quebrados e
destinos graváveis são recusados antes da parada dos serviços. O executável
resolvido precisa ser Python 3.12 ou superior e é reutilizado em modo isolado
para criar a venv do gate, sem depender do `python3` padrão da distribuição.

Antes de parar processos, o candidato passa pelo gate em worktree isolado e o
script recusa job `RUNNING`, notificação `SENDING` ou maintenance lock. O
commit anterior é persistido como `root:palmanager 0640`. Rollback exige esse
SHA completo e compatibilidade com a revisão Alembic atual; nunca ocorre por
trap, falha, downgrade ou restauração automática. Consulte o
[runbook de deploy](../operations/deploy.md).
