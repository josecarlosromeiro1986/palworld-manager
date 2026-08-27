# Palworld Manager

Aplicação web V1 para administrar um servidor dedicado de Palworld com segurança, interface privada e baixa dependência de operações rotineiras via terminal.

## Objetivos principais

- Controlar o ciclo de vida do servidor e acompanhar sua saúde.
- Consultar jogadores, enviar anúncios e executar ações administrativas pela API oficial.
- Exibir métricas e logs em tempo real.
- Gerenciar configurações, backups, restaurações e atualizações com fluxos seguros.
- Integrar backups ao Google Drive e alertas ao Discord.
- Manter auditoria, diagnóstico e acesso privado por Tailscale.

## Stack

- **Backend:** Python, FastAPI, Jinja2, SQLite, SQLAlchemy e Alembic.
- **Frontend:** HTMX, Tailwind CSS e Chart.js.
- **Desenvolvimento:** Docker Compose com aplicação web, worker e serviços simulados.
- **Testes:** Pytest para as camadas rápidas e Playwright/Chromium para poucos fluxos E2E críticos.
- **Produção:** serviços systemd independentes para web e worker, com Tailscale Serve.
- **Integrações:** REST API oficial do Palworld, SteamCMD, rclone e Discord.

## Status

> Status: V1 `1.0.0` implementada. Aplicação, integrações, instalação nativa, deploy recorrente com rollback manual, testes críticos e hardening final atendem à especificação.

## Desenvolvimento

Pré-requisitos: Git e Docker com Docker Compose. GNU Make é recomendado para os atalhos; Python, Node.js e as ferramentas de qualidade são fornecidos pelas imagens de desenvolvimento.

```bash
make dev
```

A aplicação fica disponível em `http://127.0.0.1:8080` e exige o administrador criado com `make admin-create`; o simulador mínimo fica em `http://127.0.0.1:8090`. Para encerrar os containers, execute `make down`.

```bash
make test
make check
make e2e
```

Sem GNU Make, os E2E podem ser executados diretamente com `docker compose run --build --rm e2e`.

O schema local é criado e atualizado explicitamente com `make db-upgrade`. Assets Tailwind, HTMX e ícones são compilados localmente pela imagem; `make frontend-build` permite reconstruí-los separadamente. `make e2e` usa um estágio isolado com Chromium, banco temporário e adapters fake para validar login/logout, rota privada, Stop/Restart, Restore com `RESTAURAR` e gravação de configuração. Consulte a [preparação do ambiente](docs/development/setup.md) para os demais comandos disponíveis. O worker executa jobs persistentes de ciclo de vida, desligamento e backup local, mantém heartbeat a cada 10 segundos e não expõe HTTP. O Dashboard mostra sua saúde, progresso e logs dos jobs; operações incompatíveis usam um maintenance lock global. A página **Backups** cria e acompanha backups manuais, lista somente registros locais válidos e aplica automaticamente o agendamento diário das 04:00 e retenção de 3 artefatos gerenciados. A página **Logs** oferece histórico, filtros, cópia e streaming SSE. A página **Jogadores** consulta a lista somente pelo botão de atualização, mantém o último resultado apenas em memória, envia anúncios e permite Kick, Ban e Unban com confirmação, CSRF, histórico e auditoria. **Configurações do Palworld** edita apenas campos reconhecidos do `PalWorldSettings.ini`, preserva desconhecidos, cria uma cópia pré-save e oferece Restart após a gravação. Development e test usam fakes completos, sem consultar o journald, controlar o host, tocar no INI ou mundo reais ou depender de um Palworld real.

O worker também executa `LOCAL_RESTORE`; a web apenas enfileira e acompanha o job. Na página **Backups**, o administrador confirma `RESTAURAR`, acompanha validação, backup preventivo, Stop, aplicação, Start e verificação final. O fluxo restaura somente mundo e configurações do Palworld: o banco, usuários, sessões, auditoria, jobs e configurações do Manager permanecem intactos.

O Google Drive usa o remote estrutural `RCLONE_REMOTE` e exclusivamente o
namespace `Palworld Manager/Backups/`. O backup diário válido cria upload
automático posterior; backups manuais e preventivos permanecem locais por
padrão e podem ser enviados pelo painel. Status, quota, upload, download para a
área local e exclusão remota são jobs do worker. Development e test usam um fake
completo e não executam rclone nem acessam rede.

Backups remotos válidos também oferecem **Restore remoto**. A web exige
`RESTAURAR` e apenas cria o job; o worker baixa para staging, valida SHA-256,
tar.gz, manifest e payload antes de criar o backup preventivo e tocar no
Palworld. O download não vira uma cópia local permanente, e o artefato remoto é
preservado independentemente do resultado.

A página **Atualizações** verifica manualmente os `buildid` instalado e público
do App ID `2394010` e nunca inicia Update automaticamente. Após confirmação com
`ATUALIZAR`, o worker verifica espaço e versão novamente sob lock, cria e preserva
um backup pré-update válido, executa o desligamento assistido, chama o SteamCMD
com argumentos fixos e valida Start, REST/health, versão e logs críticos. O fluxo
não executa rollback automático; development e test usam fakes sem SteamCMD ou
filesystem estrutural reais.

Notificações administrativas e de falha são persistidas no SQLite e entregues
exclusivamente pelo worker ao webhook oficial do Discord. A entrega usa claim
atômico, até 3 tentativas com backoff limitado e recuperação at least once. O
secret `DISCORD_WEBHOOK_URL` permanece somente no ambiente; development e test
usam um fake integral sem acesso à rede.

A página **Configurações do Painel** mantém uma allowlist tipada de parâmetros
operacionais de backup, horário, timezone, retenções, métricas, desligamento
assistido, disco e timeouts. Ela também troca a senha com confirmação da senha
atual, revoga todas as sessões e oferece testes de Discord e Drive que apenas
criam eventos e jobs para o worker. Secrets, paths, executáveis, serviços,
sudoers e infraestrutura Tailscale permanecem fora da interface e do SQLite.

A página **Diagnóstico** agrega checks somente leitura do Manager, Palworld,
worker, host, integrações, SQLite/migrations, versão/commit e erros recentes.
SteamCMD, Drive e Discord são representados pelos últimos resultados seguros do
worker; development e test usam fakes e não consultam o host ou a rede. O
relatório pode ser atualizado e copiado sem incluir secrets, paths estruturais
ou saídas brutas.

A página **Histórico / Auditoria** reúne ações administrativas, automáticas e de
sistema com resultado, usuário, alvo, motivo, duração e job relacionado quando
aplicável. Período, ação, resultado, origem, usuário e alvo podem ser combinados
em filtros; a listagem usa 50 registros por página e retenção de 90 dias. A
gravação e a leitura aplicam proteção defensiva contra secrets e detalhes
externos livres. Exportação CSV não faz parte da V1.

O Dashboard também permite reiniciar ou desligar o Ubuntu com CSRF, modal e
frase digitada exata. O worker adquire o maintenance lock, trata o Palworld com
Stop seguro e só então chama um dos dois comandos systemd fixos. Development e
test usam fake e nunca controlam o host; as regras sudoers exatas são
instaladas pelo procedimento de produção.

## Produção

A instalação de produção é nativa, sem Docker. Os artefatos versionados em
`ops/` criam o usuário não-root `palmanager`, diretórios protegidos, venv,
assets, configuração estrutural, duas units systemd independentes, sudoers
restrito, acesso ao journald, rclone e Tailscale Serve. Migrations e criação do
administrador fazem parte da instalação.

Siga integralmente o
[runbook de instalação](docs/operations/production-install.md). Ele valida a web
por systemd + `/health` e o worker separadamente por systemd + heartbeat no
SQLite. O worker não publica HTTP e a web escuta somente em
`127.0.0.1:8080`.

O [runbook de deploy](docs/operations/deploy.md) documenta atualização recorrente
por `deploy.sh`, gate antes da ativação, registro do commit anterior e rollback
manual compatível com a revisão Alembic. Web e worker são reiniciados e validados
separadamente; falhas nunca acionam rollback automático.

## Documentação

- [README.md](README.md): entrada rápida do projeto.
- [SPECIFICATION.md](SPECIFICATION.md): fonte de verdade dos requisitos oficiais da V1.
- [docs/index.md](docs/index.md): documentação técnica e operacional.

Em caso de conflito entre qualquer documento e `SPECIFICATION.md`, prevalece `SPECIFICATION.md`.
