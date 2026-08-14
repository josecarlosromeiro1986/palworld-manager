# Palworld Manager — Especificação Técnica da V1

> Documento de implementação para desenvolvimento com Codex.
>
> **Projeto:** Palworld Manager  
> **Repositório:** `palworld-manager`  
> **Produção:** Ubuntu Server, instalação nativa, sem Docker  
> **Desenvolvimento:** Docker Compose  
> **Idioma da V1:** Português (Brasil)  
> **Projeto privado:** sem licença de distribuição na V1

---

## 1. Objetivo

Criar uma aplicação web pequena, segura e leve para administrar um servidor dedicado de Palworld, reduzindo a dependência do terminal para tarefas rotineiras e mantendo SSH/Tailscale para administração manual e recuperação.

### Escopo principal

- Start, Stop e Restart do Palworld.
- Recuperação automática após crashes via systemd.
- Atualização manual via SteamCMD.
- Dashboard estilo administração/btop.
- Logs em tempo real.
- Consulta manual de jogadores online.
- Kick, Ban e Unban via API oficial do Palworld.
- Anúncios via API oficial.
- Editor seguro do `PalWorldSettings.ini`.
- Backup e restauração completos.
- Google Drive via rclone.
- Discord para alertas importantes.
- Auditoria.
- Diagnóstico.
- Reiniciar/desligar Ubuntu.
- Acesso privado via Tailscale Serve + HTTPS.

## 2. Princípios obrigatórios

1. Segurança por padrão.
2. O processo web nunca roda como `root`.
3. Privilégios mínimos e explícitos.
4. Operações destrutivas interrompidas nunca são retomadas automaticamente.
5. Não manipular saves individuais de jogadores na V1.
6. Preferir APIs/interfaces oficiais do Palworld.
7. Sem terminal web.
8. Sem gerenciamento de mods.
9. Sem scraping da documentação do Palworld em runtime.
10. Não duplicar logs do Palworld no SQLite.
11. Não persistir métricas históricas em disco.
12. Segredos nunca ficam no SQLite.
13. Preservar configurações desconhecidas do Palworld.
14. Uma etapa só termina quando testes e critérios de aceite passarem.
15. Um commit Git por etapa concluída.

## 3. Stack

### Backend
- Python 3
- FastAPI + Uvicorn
- Jinja2
- SQLAlchemy 2.x + Alembic
- Pydantic Settings
- SQLite
- psutil
- Argon2id para hashes de senha

### Frontend
- Jinja2 server-side rendering
- HTMX
- SSE (Server-Sent Events)
- Tailwind CSS
- Chart.js
- JavaScript mínimo, sem React/Vue

### Qualidade
- Ruff
- Pytest
- Mypy
- pre-commit
- Playwright apenas para poucos fluxos E2E críticos
- `make check` como gate principal

### Desenvolvimento
Docker Compose com três containers planejados:

```text
app
worker
mock-services
```

`app` executa o FastAPI. `worker` consome e executa jobs persistidos. Ambos podem usar a mesma imagem da aplicação, iniciada com comandos diferentes, e compartilhar o SQLite em volume persistente. `mock-services` simula REST API do Palworld, Discord e serviços externos necessários.

### Produção

```text
venv + systemd + Tailscale Serve
```

Sem Docker. A aplicação web e o worker rodam como serviços systemd independentes, `palworld-manager.service` e `palworld-manager-worker.service`. Node/npm somente para build dos assets; Node não roda como serviço em produção.

## 4. Estrutura sugerida

```text
palworld-manager/
├── app/
│   ├── auth/
│   ├── dashboard/
│   ├── players/
│   ├── backups/
│   ├── updates/
│   ├── settings/
│   ├── diagnostics/
│   ├── audit/
│   ├── jobs/
│   ├── integrations/
│   │   ├── palworld_api/
│   │   ├── discord/
│   │   ├── google_drive/
│   │   └── tailscale/
│   ├── system/
│   ├── templates/
│   ├── static/
│   └── main.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── mock-services/
├── migrations/
├── scripts/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── package.json
├── Makefile
├── deploy.sh
└── README.md
```

Backend modular por domínio; evitar `main.py` gigante.

## 5. Ambientes

- **development:** Docker Compose, Palworld/Discord/Drive simulados.
- **test:** SQLite isolado/temporário e integrações simuladas.
- **production:** Ubuntu, Palworld real, API oficial, systemd, SteamCMD, journald, rclone, Discord e Tailscale Serve.

## 6. Configuração

### Estrutural — não editável pelo painel

```text
PALWORLD_SERVICE=palworld.service
PALWORLD_REST_BASE_URL=http://127.0.0.1:8212/v1/api
PALWORLD_DIR=/home/steam/palserver
PALWORLD_SETTINGS=/home/steam/palserver/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini
STEAMCMD=/usr/games/steamcmd
APP_HOST=127.0.0.1
APP_PORT=8080
MANAGER_DATABASE=/var/lib/palworld-manager/manager.db
```

Usar Pydantic Settings para tipagem e validação no startup.

O banco SQLite persistente fica em `/var/lib/palworld-manager/manager.db` em produção, separado do código em `/opt/palworld-manager` e dos secrets em `/etc/palworld-manager`. Web e worker acessam o mesmo arquivo. Em desenvolvimento, um volume Docker persistente é montado no mesmo caminho dentro dos containers `app` e `worker`.

### Operacional — SQLite e editável no painel

Defaults:

```text
timezone = America/Sao_Paulo
backup_enabled = true
backup_time = 04:00
local_backup_retention = 3
drive_backup_retention = 10
metrics_interval_seconds = 5
assisted_shutdown_default_minutes = 5
start_timeout_seconds = 120
restart_timeout_seconds = 120
stop_timeout_seconds = 60
disk_warning_gb = 20
disk_critical_gb = 10
```

Timestamps persistidos em UTC e exibidos no timezone configurado.

### Segredos

Produção:

```text
/etc/palworld-manager/secrets.env
```

Permissão restrita ao `palmanager`. Exemplos: credencial da REST API do Palworld, Discord webhook e demais segredos. A UI pode testar/substituir, mas nunca revelar integralmente.

As credenciais da REST API oficial são fornecidas exclusivamente pelos secrets:

```text
PALWORLD_REST_USERNAME
PALWORLD_REST_PASSWORD
```

Ambas são obrigatórias em `production`; configuração ausente ou inválida deve impedir o startup com uma mensagem clara e sem revelar os valores. Não existe username padrão nem fallback para `admin`. Em `development` e `test`, toda a integração REST do Palworld é substituída por um fake e não exige credenciais.

## 7. Usuário Linux e privilégios

Usuário dedicado:

```text
palmanager
```

Diretório:

```text
/opt/palworld-manager
```

Serviços:

```text
palworld-manager.service
palworld-manager-worker.service
```

Ambos são executados como `palmanager`, usam a configuração estrutural apropriada e acessam o mesmo SQLite quando necessário. Nunca executar a aplicação web ou o worker como root. `sudoers` deve liberar somente comandos/scripts estritamente necessários, sem `sudo ALL`. Preferir scripts controlados e argumentos validados.

## 8. Acesso

FastAPI escuta apenas em `127.0.0.1:<porta>`. Publicação privada via **Tailscale Serve + HTTPS**. Sem Funnel na V1. Controle de dispositivos fica no Tailscale; não criar whitelist duplicada. SSH via Tailscale permanece disponível.

## 9. Autenticação e segurança de sessão

V1 com um administrador, mas modelo preparado para múltiplos usuários futuros.

- senha mínima: 6 caracteres;
- Argon2id;
- nunca armazenar senha em texto puro;
- sessão server-side no SQLite;
- cookie com identificador opaco;
- duração máxima: 8 horas;
- inatividade máxima: 1 hora;
- logout invalida sessão;
- troca de senha invalida todas as sessões;
- cookies `HttpOnly` e `SameSite=Strict` em todos os ambientes; o cookie de sessão usa `Secure` obrigatoriamente em produção e pode omiti-lo somente em development/test para permitir o acesso HTTP local;
- 5 tentativas erradas consecutivas para o mesmo usuário → bloqueio desse usuário por 15 minutos; um login bem-sucedido ou a expiração do bloqueio reinicia a contagem; o endereço de origem observado é registrado para auditoria, mas não compõe a chave do bloqueio;
- tentativas/bloqueios auditados;
- sem CAPTCHA;
- CSRF obrigatório em toda operação que altera estado.

Tudo autenticado por padrão, exceto `/login` e `/health`. `/health` é exclusivo da aplicação web e deve retornar apenas estado mínimo, por exemplo `{"status":"ok"}`. O worker não expõe servidor HTTP próprio.

Recuperação de senha somente via terminal, com CLI equivalente a:

```bash
python -m app.cli reset-password
```

## 10. Navegação e interface

Menu:

1. Dashboard
2. Jogadores
3. Logs
4. Backups
5. Atualizações
6. Configurações do Palworld
7. Configurações do Painel
8. Diagnóstico
9. Histórico / Auditoria

Interface Dark, compacta, técnica, responsiva, prioridade desktop e utilizável no celular.

## 11. Dashboard

Mostrar status do Palworld, CPU host/processo, RAM, disco, uptime Ubuntu/serviço, versão Palworld, versão Manager + commit Git, último backup, Drive, rede, IP/estado Tailscale, Serve, trecho de log e último resultado conhecido de jogadores.

Ações rápidas: Start, Stop, Restart, Backup agora e Enviar anúncio.

Métricas via psutil, HTMX a cada 5 s. Buffer circular somente em memória por 15 min para CPU/RAM/rede. Gráficos via Chart.js.

## 12. Health check do Palworld

Estados:

```text
ONLINE
INICIANDO
DEGRADADO
OFFLINE
FALHA
```

`systemctl active` sozinho não basta. Combinar systemd, processo e REST API. Reutilizar o health check em Start, Restart, Update, Restore e Diagnóstico.

O probe REST do health check consulta `GET /info` a partir de `PALWORLD_REST_BASE_URL`, usando HTTP Basic Auth com `PALWORLD_REST_USERNAME` e `PALWORLD_REST_PASSWORD`. Credenciais nunca podem aparecer na URL, em logs ou em mensagens de erro.

## 13. Controle do serviço

### Start / Restart
`systemctl` → aguardar processo → REST API → health check → ONLINE. Timeout 120 s.

Start e Restart são jobs persistentes, exigem confirmação na UI e usam respectivamente `start_timeout_seconds` e `restart_timeout_seconds`. Apenas o worker executa o comando; FastAPI cria e acompanha o job.

### Stop
Aviso assistido quando aplicável → `systemctl stop` → aguardar processo → confirmar `inactive` → confirmar portas relevantes fechadas → OFFLINE. Timeout 60 s.

O Stop é um job persistente, exige confirmação na UI e usa `stop_timeout_seconds`. A conclusão requer health `OFFLINE` e a porta da REST API configurada fechada. A lógica de avisos a jogadores e encerramento forçado pertence à etapa de desligamento assistido.

Nunca usar `kill -9` automaticamente.

### Encerramento forçado
Só após Stop falhar. Primeiro nível exige digitar `FORCAR` e tenta SIGTERM. SIGKILL somente como último recurso, com segunda confirmação. Auditar e alertar Discord.

SIGTERM não é liberado quando a falha ocorreu antes do Stop, por exemplo ao consultar ou avisar jogadores. SIGKILL exige que o job manual de SIGTERM também tenha falhado e a segunda confirmação consiste em digitar `SIGKILL`. Os sinais são enviados somente ao processo principal da unidade `PALWORLD_SERVICE`, através de argumentos fixos do systemd; não há escalada automática entre os níveis.

## 14. Recuperação de crashes

Usar systemd para reinício automático com limite anti-loop. Stop administrativo deve permanecer parado. Falhas recorrentes geram estado crítico, auditoria e Discord.

## 15. Desligamento assistido

Opções: Agora, 1, 5 e 10 minutos; default 5. Consultar jogadores quando necessário, enviar avisos oficiais, mostrar progresso, permitir cancelar em ponto seguro e permitir “Forçar agora” quando apropriado.

O desligamento consulta `GET /players` uma vez no início. Quando houver jogadores online, envia por `POST /announce` um aviso inicial e outro imediatamente antes do Stop; cancelamento durante a contagem tenta informar que a operação foi cancelada. Falha na consulta ou em um aviso obrigatório encerra o job sem executar o Stop. “Forçar agora” somente ignora o restante da contagem e segue pelo Stop normal; não envia SIGTERM nem SIGKILL.

## 16. Jogadores

Somente REST API oficial. Não ler saves para jogadores offline.

Sem polling. Consultar apenas ao clicar **Atualizar jogadores** ou quando uma operação precisar verificar conectados. Cache somente em memória com timestamp; após restart: “Ainda não consultado”.

Mostrar campos oficiais úteis, incluindo nome, level, IDs necessários, plataforma/conta e ping quando disponíveis.

Erros amigáveis: servidor offline, API indisponível, autenticação, timeout, resposta inválida, inesperado.

## 17. Kick, Ban e Unban

- Kick: motivo opcional.
- Ban: motivo obrigatório em texto livre, sem motivos predefinidos.
- Unban: motivo obrigatório em texto livre.
- Registrar alvo, `userId`, usuário, motivo e resultado.
- SQLite mantém histórico administrativo; não substitui a autoridade do Palworld.

## 18. Anúncios

Texto livre, contador de caracteres, confirmação com mensagem exata, envio via API oficial e auditoria. Sem agendamento na V1, exceto mensagens automáticas do desligamento assistido.

## 19. Logs do Palworld

Fonte: `journald` do `palworld.service`.

- 100/500/1000 linhas anteriores;
- SSE em tempo real;
- pausa/autoscroll;
- pesquisa/filtro;
- copiar trecho;
- classificação visual ERROR/WARNING/conexão/sistema/normal;
- preservar texto original;
- não duplicar no SQLite.

## 20. Configurações do Palworld

Formulário tipado para `PalWorldSettings.ini`, sem editor livre como fluxo principal.

Regras: ler arquivo real, interpretar conhecidos, validar, backup do INI antes de salvar, preservar desconhecidos, sinalizar novos/desconhecidos, não editar desconhecidos, indicar necessidade de Restart e oferecer Restart com confirmação.

Definições versionadas no projeto e baseadas na documentação oficial. Sem scraping em runtime.

## 21. Configurações do Painel

Editar somente parâmetros operacionais seguros: backup, horário, retenção, timezone, métricas, aviso assistido, limites de disco, timeouts, senha, teste Discord e Drive.

Não permitir alterar pela UI `sudoers`, serviços arbitrários, executáveis arbitrários, infraestrutura Tailscale ou caminhos críticos livres.

## 22. Jobs em background

O worker é um processo separado da aplicação web em produção:

```text
Web:              palworld-manager.service
Worker:           palworld-manager-worker.service
Fila/coordenação: SQLite
```

`palworld-manager.service` executa FastAPI, autenticação, sessões, Jinja2, HTMX, SSE, páginas, consultas, criação e acompanhamento de jobs, Dashboard e interação do usuário. O serviço web não executa diretamente operações longas ou destrutivas que pertencem ao worker.

`palworld-manager-worker.service` consome e executa jobs persistidos no SQLite, incluindo backup, restore, update via SteamCMD, upload para Google Drive, download de backup remoto, desligamento assistido e outras operações críticas ou demoradas. O worker registra estado, progresso, timestamps, resultado e referência ao log da operação.

Na V1, o SQLite é o mecanismo persistente de coordenação e fila entre FastAPI e worker. Não usar Redis, Celery, RabbitMQ ou Kafka. A aquisição de jobs deve ser atômica e transacional para impedir que dois workers executem o mesmo job. A UI acompanha etapas, progresso e log.

O reinício apenas do processo web não interrompe o worker; um job já em execução continua quando isso for seguro. Se o worker reiniciar durante um job, deve localizar jobs deixados em `running`, marcá-los ou reconciliá-los como `interrupted`, verificar o estado real e nunca retomar automaticamente uma operação destrutiva. Situações ambíguas exigem intervenção humana e alerta quando relevante.

### Health check do worker

`palworld-manager-worker.service` não expõe servidor HTTP. Sua saúde combina o estado do serviço no systemd com um heartbeat persistido no SQLite e atualizado pelo worker a cada 10 segundos:

```text
systemd ativo + sem heartbeat + ativação < 30 segundos  → STARTING
systemd ativo + sem heartbeat + ativação >= 30 segundos → UNRESPONSIVE
systemd ativo + heartbeat < 30 segundos                  → HEALTHY
systemd ativo + heartbeat >= 30 segundos                 → UNRESPONSIVE
systemd inativo                                           → OFFLINE
```

O período `STARTING` evita falso alerta enquanto o worker inicializa. Se nenhum heartbeat for persistido até completar 30 segundos desde a ativação do serviço, o estado passa a `UNRESPONSIVE`.

O deploy valida os dois processos separadamente: `palworld-manager.service` por systemd + `/health`; `palworld-manager-worker.service` por systemd + heartbeat no SQLite.

## 23. Maintenance lock

Lock global para operações incompatíveis como Update, Restore, backup pré-update, Restart assistido e shutdown/reboot. Durante lock, logs, métricas, diagnóstico e consultas seguras continuam disponíveis.

## 24. Cancelamento seguro

Cancelável em contagem regressiva, backup antes de etapa crítica e upload. Não cancelar SteamCMD modificando arquivos, Restore substituindo saves ou etapas onde interrupção gere estado incerto. UI deve informar quando não for mais seguro cancelar.

## 25. Backups

Automático diário às 04:00, timezone inicial `America/Sao_Paulo`, configurável.

Não derrubar jogadores para backup diário. Solicitar salvamento seguro pelo mecanismo oficial disponível antes de copiar. Se falhar, não marcar backup como válido.

Conteúdo: mundo completo (`Level.sav`, `LevelMeta.sav`, `Players/` e demais dados persistentes), `PalWorldSettings.ini`, configs relevantes, cópia consistente de `manager.db`, configurações não sensíveis e `manifest.json`.

Não incluir binários, SteamCMD, `secrets.env`, tokens/webhooks/credenciais ou a pasta de backups internos do Palworld recursivamente.

Formato: `.tar.gz`. Integridade: teste do arquivo + SHA-256. Backup inválido é auditado e removido.

Retenção: 3 locais e até 10 no Drive.

## 26. Google Drive via rclone

Configuração inicial do rclone manual via terminal. Painel testa conexão, quota, lista/envia/baixa/remove backups gerenciados.

Pasta própria conceitual: `Palworld Manager/Backups/`.

**Regra absoluta:** nunca excluir arquivos fora da pasta/namespace gerenciado pelo Palworld Manager.

Antes do upload: verificar quota, aplicar retenção apenas própria, remover backup próprio antigo se necessário; se ainda faltar espaço, cancelar upload, preservar local, auditar e alertar Discord. Não exigir plano pago.

## 27. Restauração

Local ou Drive. Exigir digitar `RESTAURAR`.

Fluxo: lock → validar backup → download temporário se remoto → SHA-256/integridade → backup preventivo → Stop seguro → substituir arquivos → ownership/permissões → Start → REST API → health check → verificar erros críticos → concluir somente saudável → limpar temporários quando seguro.

Sem rollback automático. Preservar backup preventivo em falha. Sem restauração isolada de jogador.

## 28. Atualizações do Palworld

Somente manual, com botão **Verificar atualizações**. Mostrar versão instalada/disponível e data quando confiável. Sem changelog de terceiros.

Update: espaço → verificar → lock → backup pré-update → shutdown assistido → Stop → SteamCMD → Start → REST API → health check → versão → erros críticos → resultado → auditoria → Discord. Timeout pós-start 120 s. Sem rollback automático; preservar backup pré-update.

## 29. Espaço em disco

- warning: menos de 20 GB livres;
- critical: menos de 10 GB livres.

Warning aparece no Dashboard. Critical bloqueia operações que agravem consumo quando apropriado e notifica Discord. Restore calcula/verifica espaço específico antes de iniciar.

## 30. Discord

Webhook, sem bot permanente. Política enxuta: crash sem recuperação, recuperação pós-crash, backup automático falhou, Drive falhou, disco crítico, Update/Restore concluído ou falhou, login bloqueado, operação crítica interrompida, encerramento forçado e eventos críticos equivalentes. Sucessos rotineiros ficam só na auditoria.

Qualquer componente, incluindo FastAPI e worker, pode criar um `notification_event` no SQLite. Somente o worker pode realizar a entrega externa ao Discord; FastAPI nunca envia notificações diretamente ao webhook.

Estados conceituais de entrega:

```text
PENDING
SENDING
SENT
FAILED
```

Falhas transitórias permitem no máximo 3 tentativas totais, com pequeno backoff. Após esgotar as tentativas, o estado é `FAILED`; não existe retry infinito. Os detalhes exatos do backoff serão definidos durante a implementação.

A entrega tem semântica **at least once**. Se o worker reiniciar e encontrar um `notification_event` deixado em `SENDING` pelo processo anterior, deve reconciliá-lo: com menos de 3 tentativas, retorna a `PENDING` e a próxima entrega conta como nova tentativa; com 3 tentativas, passa a `FAILED`. Uma mensagem pode ser entregue mais de uma vez se o Discord a tiver aceitado antes da interrupção, mas o worker não tiver persistido `SENT`.

## 31. Reiniciar/desligar Ubuntu

Permitir com confirmação forte. Antes, tratar Palworld de forma segura, avisar que painel ficará indisponível e auditar. Sudoers estritamente limitado.

## 32. Diagnóstico

Somente leitura. Verificar os serviços web, worker e Palworld, processos, portas, REST API, health do worker por systemd + heartbeat no SQLite, disco/RAM, permissões/diretórios, SteamCMD, conectividade necessária, Tailscale/Serve, rclone/Drive, Discord, SQLite/migrations, versão/commit e erros relevantes.

Mostrar ✓ OK, ⚠ Atenção, ✗ Falha. Botões Testar novamente e Copiar diagnóstico. Nunca incluir segredos.

## 33. Auditoria

Retenção 90 dias. Registrar timestamp UTC, ação, resultado, origem, usuário, alvo, motivo, duração, detalhes e job. Origem: Administrador/Automático/Sistema. Resultado: Sucesso/Falha/Cancelada/Interrompida. Nunca registrar senha/token/webhook/segredo.

Filtros por período, ação, resultado, origem, usuário e alvo. Paginação 50/página. Sem exportação CSV na V1.

## 34. Logs de jobs

Logs textuais separados; SQLite guarda metadados/referência. Retenção 90 dias. Exemplo: `jobs/2026/update-000123.log`.

## 35. Observabilidade do Manager

Usar journald para `palworld-manager.service` e `palworld-manager-worker.service`, com nível, módulo e request/job id quando aplicável. Sem Sentry/Prometheus/Grafana na V1.

## 36. Banco

SQLite + SQLAlchemy 2.x + Alembic. Modelo preparado para múltiplos usuários.

Entidades sugeridas: `users`, `sessions`, `login_attempts`, `app_settings`, `audit_events`, `notification_events`, `jobs`, `backup_records`, `ban_history`.

`audit_events` é a trilha do que aconteceu no sistema, quem ou qual origem executou e qual foi o resultado. Exemplos de ações: `UPDATE_SERVER`, `BACKUP`, `RESTORE`, `BAN`, `UNBAN` e `LOGIN_BLOCKED`.

`notification_events` é uma única entidade conceitual para controlar e registrar entregas destinadas a integrações externas, inicialmente Discord. Qualquer componente pode criar o evento, mas somente o worker realiza a entrega. Registra somente dados necessários à entrega, como tipo do evento, canal, status (`PENDING`, `SENDING`, `SENT` ou `FAILED`), horário, job relacionado, tentativas e erro de entrega quando houver. A entrega é at least once e eventos `SENDING` deixados por um worker interrompido são reconciliados conforme o limite de 3 tentativas. Exemplos de tipos: `BACKUP_FAILED`, `RESTORE_FAILED`, `UPDATE_COMPLETED`, `UPDATE_FAILED`, `DISK_CRITICAL`, `SERVER_CRASH`, `SERVER_RECOVERED`, `FORCED_SHUTDOWN` e `LOGIN_BLOCKED`.

Um `audit_event` com ação `UPDATE_SERVER`, ator `admin` e resultado `FAILURE` pode originar um `notification_event` do tipo `UPDATE_FAILED`, canal `DISCORD` e status `SENT`. `notification_events` não duplica toda a auditoria. O schema SQL definitivo será definido na etapa apropriada de implementação.

## 37. Sem API REST própria na V1

Rotas Jinja2/HTMX chamam serviços Python diretamente. Lógica de negócio desacoplada das views para permitir API futura.

## 38. HTMX e SSE

HTMX para métricas, formulários, ações e fragmentos. SSE para logs e progresso/estado que se beneficie de push. Sem WebSocket na V1.

## 39. Hardening obrigatório

Validar inputs; nunca interpolar input em shell; preferir `subprocess` com lista e `shell=False`; paths allowlisted; proteger contra path traversal/symlinks e tar traversal; limites razoáveis; sem stack trace na UI; mascarar segredos; transações SQLite; proteção contra double-submit; lock consistente; confirmações obrigatórias.

## 40. Fora da V1

Terminal web, mods, jogadores offline por save, restore individual de jogador, monitoramento contínuo de login/logout, notificações de entrada/saída, scraping docs, React/Vue, Redis/Celery/RabbitMQ/Kafka, Prometheus/Grafana/Sentry, API própria, múltiplos admins na UI, roles, exportação de auditoria, anúncios agendados, criptografia de backups, rollback automático de Restore/Update, GitHub Actions obrigatório e auto-update do Manager.

# 41. Plano incremental para o Codex

Para cada etapa: implementar somente o escopo; adicionar testes; rodar `make check`; corrigir falhas; validar aceite; atualizar README quando necessário; fazer um commit coerente; só então avançar.

### Etapa 1 — Bootstrap
FastAPI mínimo, estrutura, Docker/Compose, mock-services mínimo, Makefile, Ruff/Mypy/Pytest/pre-commit, README. **Aceite:** dev sobe e `/health` responde. Commit: `chore: bootstrap palworld manager`.

### Etapa 2 — Configuração e ambientes
Pydantic Settings e development/test/production. **Aceite:** config inválida impede startup sem vazar segredos. Commit: `feat: add application configuration`.

### Etapa 3 — Banco e migrations
SQLite, SQLAlchemy, Alembic e modelos base. **Aceite:** banco novo nasce via migrations e testes isolados. Commit: `feat: add database foundation`.

### Etapa 4 — Administrador e Argon2id
Usuário inicial/CLI e hash. **Aceite:** senha nunca em texto puro. Commit: `feat: add administrator account`.

### Etapa 5 — Login, sessão e CSRF
8h/1h, cookies e rotas privadas. **Aceite:** acesso privado exige sessão. Commit: `feat: add secure authentication`.

### Etapa 6 — Brute force e auditoria básica
5 tentativas/15 min. **Aceite:** bloqueio funciona e é auditado. Commit: `feat: add login protection and audit foundation`.

### Etapa 7 — Layout Dark
Jinja2/HTMX/Tailwind, responsividade e menu. **Aceite:** páginas-base desktop/mobile. Commit: `feat: add dashboard layout`.

### Etapa 8 — Métricas
psutil, buffer 15 min, HTMX e Chart.js. **Aceite:** CPU/RAM/disco/rede sem persistência. Commit: `feat: add host metrics`.

### Etapa 9 — systemd Palworld
Abstração segura e fake dev. **Aceite:** estado ativo/inativo consultável. Commit: `feat: add palworld service integration`.

### Etapa 10 — Health check
Estados completos e REST API. **Aceite:** testes unitários das combinações principais. Commit: `feat: add palworld health checks`.

### Etapa 11 — Start/Stop/Restart
Jobs, timeouts e confirmações. **Aceite:** mocks respeitam 120/60 s configuráveis. Commit: `feat: add server lifecycle controls`.

### Etapa 12 — Shutdown assistido/forçado
Agora/1/5/10, cancelamento, SIGTERM/SIGKILL manual. **Aceite:** nenhum SIGKILL automático. Commit: `feat: add assisted shutdown`.

### Etapa 13 — Logs
journald, histórico, filtros, SSE e copiar trecho. **Aceite:** streaming reconecta corretamente. Commit: `feat: add server log viewer`.

### Etapa 14 — REST API Palworld
Cliente tipado, erros, mock, jogadores manual e anúncios. **Aceite:** sem polling contínuo. Commit: `feat: add palworld rest integration`.

### Etapa 15 — Kick/Ban/Unban
Motivos e histórico. **Aceite:** Ban/Unban exigem motivo; Kick não. Commit: `feat: add player administration`.

### Etapa 16 — Editor INI
Parser/serializer conservador, schemas e backup pré-save. **Aceite:** parâmetros desconhecidos nunca são descartados. Commit: `feat: add palworld settings editor`.

### Etapa 17 — Jobs/lock completos
Fila persistente no SQLite, worker separado, aquisição atômica, heartbeat, progresso, logs, recovery e maintenance lock. **Aceite:** jobs não executam duas vezes, destrutivos não retomam automaticamente e health do worker cobre `STARTING`, `HEALTHY`, `UNRESPONSIVE` e `OFFLINE`. Commit: `feat: add persistent job system`.

### Etapa 18 — Backup local
Save seguro, tar.gz, manifest, SHA-256, integridade, retenção 3. **Aceite:** inválido não é restaurável. Commit: `feat: add local backups`.

### Etapa 19 — Restore local
`RESTAURAR`, backup preventivo, validação e health check. **Aceite:** sem rollback automático. Commit: `feat: add backup restore`.

### Etapa 20 — Google Drive/rclone
Status, quota, pasta própria, upload/download/delete e retenção 10. **Aceite:** testes garantem que nada fora do namespace próprio é removido. Commit: `feat: add google drive backups`.

### Etapa 21 — Restore remoto
Download temporário + fluxo seguro. **Aceite:** valida antes de tocar no mundo. Commit: `feat: add remote backup restore`.

### Etapa 22 — SteamCMD Update
Verificação manual, backup, shutdown, update e health check. **Aceite:** sem update/rollback automático. Commit: `feat: add palworld updates`.

### Etapa 23 — Discord
Webhook, entrega exclusiva pelo worker, estados persistentes, semântica at least once e até 3 tentativas. **Aceite:** FastAPI não envia diretamente ao Discord, `SENDING` interrompido é reconciliado, retry não é infinito e segredo não aparece em UI/log/auditoria. Commit: `feat: add discord notifications`.

### Etapa 24 — Configurações do Painel
Operacionais, timezone, retenção, limites e testes de integração. **Aceite:** infraestrutura perigosa não é editável. Commit: `feat: add manager settings`.

### Etapa 25 — Diagnóstico
Checks read-only e relatório copiável. **Aceite:** sem segredos. Commit: `feat: add diagnostics`.

### Etapa 26 — Auditoria completa
Filtros, 50/página, 90 dias, usuário/origem/alvo/motivo. **Aceite:** trilha consistente. Commit: `feat: complete audit history`.

### Etapa 27 — Power controls Ubuntu
Reboot/shutdown e permissões mínimas. **Aceite:** sem injeção de comandos. Commit: `feat: add host power controls`.

### Etapa 28 — Playwright crítico
Login/logout, rota protegida, Stop/Restart mock, Restore `RESTAURAR`, salvar config. **Aceite:** E2E críticos passam. Commit: `test: add critical browser flows`.

### Etapa 29 — Deploy produção
`palmanager`, venv, serviços systemd web/worker, heartbeat do worker, sudoers, assets, migrations, Tailscale Serve, journald, rclone. **Aceite:** sem Docker, sem root e sem servidor HTTP no worker. Commit: `ops: add production deployment`.

### Etapa 30 — deploy.sh + rollback
Registrar commit anterior, atualizar, dependências, assets, migrations, config, checks, restart, validar web por systemd + `/health` e worker por systemd + heartbeat; rollback manual. **Aceite:** os dois serviços são validados separadamente e rollback nunca é automático. Commit: `ops: add safe deployment rollback`.

### Etapa 31 — Hardening e V1
Revisão completa de permissões, secrets, CSRF, sessão, shell/path/tar, concorrência, locks, logs, retenção, timeouts, testes e docs. **Aceite:** `make check` passa; versão `1.0.0`. Commit: `release: palworld manager 1.0.0`.

## 42. Testes

Unitários: health states do Palworld e worker, inclusive ausência inicial de heartbeat, config, retenção, quota, locks, cancelamento, INI, auth, timeouts, auditoria e estados/retries de notificação.

Integração: SQLite, migrations, jobs, heartbeat do worker, reconciliação de notificações `SENDING`, entrega at least once, backup, restore, Palworld fake, rclone fake e Discord fake.

E2E: somente fluxos críticos definidos; não cobrir cada botão.

## 43. Makefile

Fornecer, no mínimo:

```text
make dev
make test
make lint
make format
make typecheck
make check
make e2e
```

`make check` é o gate antes de commit/deploy.

## 44. Git e versionamento

Semantic Versioning. Desenvolvimento `0.x.y`; V1 estável `1.0.0`. Mostrar versão + commit no Diagnóstico. Um commit por etapa concluída.

## 45. README.md

Manter README prático com visão rápida, pré-requisitos, Docker dev, Make, testes, migrations, mocks, config, deploy Ubuntu, Tailscale Serve, rclone, Discord, rollback e troubleshooting.

## 46. Instruções explícitas para o Codex

1. Leia este documento inteiro antes de modificar o projeto.
2. Segurança é requisito, não sugestão.
3. Implemente uma etapa por vez.
4. Inspecione o repositório antes de cada etapa.
5. Não reescreva módulos funcionais sem necessidade.
6. Prefira mudanças pequenas e revisáveis.
7. Não invente endpoints/comportamentos do Palworld; confirme documentação oficial ao implementar integração real.
8. Isole integrações externas em adapters testáveis.
9. Dev/test nunca dependem do servidor real.
10. Não execute comandos destrutivos no host de desenvolvimento.
11. Nunca use `shell=True` com input do usuário.
12. Nunca grave segredos em logs, banco, fixtures ou commits.
13. Nunca conceda root genérico.
14. Proteja extração de backup contra traversal/symlinks.
15. Preserve parâmetros desconhecidos do INI.
16. Não implemente fora do escopo sem solicitação.
17. Atualize testes junto da funcionalidade.
18. Rode `make check`.
19. Atualize documentação relevante após sucesso.
20. Faça um commit único e claro por etapa.
21. Pare e reporte quando depender de informação real não simulável com segurança.
22. Não “corrija” saves automaticamente em situação ambígua.
23. Operação destrutiva interrompida exige decisão humana.
24. UI amigável; detalhes técnicos em logs/diagnóstico.
25. Produção deve permanecer leve.

## 47. Critérios gerais de aceite V1

Login seguro; app como `palmanager`; Tailscale Serve; Dashboard; métricas; health check web e worker; lifecycle; crash recovery; logs; jogadores sob demanda; anúncio/Kick/Ban/Unban; INI conservador; jobs; lock; backup + SHA-256; Drive; retenções; Restore local/remoto; Update manual; Discord entregue somente pelo worker; Diagnóstico; Auditoria; reboot/shutdown; segredos protegidos; Pytest/Playwright; deploy/rollback; README; `make check`; versão 1.0.0.

## 48. Roadmap pós-V1

Múltiplos administradores, roles, API `/api/v1`, notificações adicionais, monitoramento de jogadores, mods, GitHub Actions, métricas persistentes opcionais, outros destinos de backup, internacionalização e autenticação externa.

## 49. Resumo

```text
Nome                         Palworld Manager
Produção                     Ubuntu nativo, sem Docker
Desenvolvimento              Docker Compose
Containers dev               app + worker + mock-services
Backend                      FastAPI
Frontend                     Jinja2 + HTMX + Tailwind
Tempo real                   SSE + HTMX
Gráficos                     Chart.js
Banco                        SQLite
ORM                          SQLAlchemy 2.x
Migrations                   Alembic
Config                       Pydantic Settings
Métricas                     psutil
Usuário Linux                palmanager
Acesso                       Tailscale Serve + HTTPS
Idioma                       Português (Brasil)
Tema                         Dark
Backup                       diário 04:00
Retenção local               3
Retenção Drive               10
Formato                      tar.gz
Integridade                  SHA-256 + teste do arquivo
Drive                        rclone
Criptografia backup          não na V1
Discord                      webhook, notificações enxutas
Players                      atualização manual
Logs                         journald + SSE
Auditoria                    90 dias
Serviço web                  palworld-manager.service
Serviço worker               palworld-manager-worker.service
Health worker                STARTING/HEALTHY/UNRESPONSIVE/OFFLINE
Jobs                         SQLite + worker separado
Notificações                 worker, at least once, máximo 3 tentativas
Métricas históricas          15 min em memória
Start/Restart timeout        120 s
Stop timeout                 60 s
Disco warning                < 20 GB
Disco crítico                < 10 GB
Senha mínima                 6 caracteres
Hash                         Argon2id
Tentativas login             5
Bloqueio                     15 min
Sessão máxima                8 h
Inatividade                  1 h
CSRF                         obrigatório
Terminal web                 fora da V1
Mods                         fora da V1
API própria                  fora da V1
Testes                       Pytest + poucos Playwright
Qualidade                    Ruff + Mypy + pre-commit
Validação                    make check
Versionamento                SemVer
Deploy                       deploy.sh manual
Rollback Manager             manual para commit anterior
Commits                      1 por etapa
```
