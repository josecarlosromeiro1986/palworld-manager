# Testes

> Status: Implementado para a V1 `1.0.0` e ampliado na Etapa 32. Pytest, gate completo, fluxos Playwright críticos e regressões de deploy/hardening/RBAC estão disponíveis.

Pytest é a base da suíte automatizada.

- **Unitários:** regras de negócio, validações, estados de health, locks, retenção, autenticação, cancelamento e parsing.
- **Integração:** SQLite, migrations, jobs, backups e adapters contra serviços simulados.
- **E2E:** poucos fluxos críticos no navegador com Playwright, como autenticação e confirmações destrutivas essenciais.

Playwright não deve cobrir cada botão. A maior parte do comportamento deve ser validada por testes rápidos e determinísticos abaixo da camada de interface.

As regressões dos fragmentos de jobs verificam também que o estado aberto de "Log do job" é preservado durante as atualizações periódicas do HTMX.

Comandos disponíveis:

```bash
make test
make check
make e2e
```

`make check` recompila e valida os assets de frontend, executa Ruff, verificação de formatação, Mypy e Pytest dentro do container. A configuração de pre-commit pode ser executada com `make precommit` e inclui o mesmo gate de frontend. Os E2E são executados separadamente por `make e2e` ou, sem GNU Make, por `docker compose run --build --rm e2e`.

Os testes de configuração isolam variáveis de ambiente e arquivos locais. A suíte de integração também verifica que uma configuração inválida impede o startup sem reproduzir o valor recebido na saída de erro.

Testes de banco usam um `manager.db` temporário por caso e executam `alembic upgrade head`. Eles verificam schema, revisão, pragmas SQLite, integridade referencial e commit/rollback sem tocar no volume de desenvolvimento.

Os testes de credenciais verificam o formato Argon2id, a senha mínima, rejeição
de hashes inválidos, bootstrap do administrador e redefinição pela CLI. A suíte
de RBAC cobre login/username case-insensitive, senha temporária, allowlist do
`USER` com 403 contra bypass direto, criação e gestão pelo `ADMIN`, revogação
de sessões, proteção da própria conta e do último administrador ativo e autoria
dos jobs de desligamento. Bancos temporários confirmam que senhas em texto puro
não são persistidas nem exibidas.
As regressões também rejeitam a reutilização da senha temporária e confirmam
que a recuperação pela CLI preserva contas USER.

Os testes de autenticação cobrem rotas privadas por padrão, login e logout, CSRF, atributos dos cookies, revogação por troca de senha e limites exatos de 8 horas totais e 1 hora de inatividade. Também verificam o bloqueio na quinta falha consecutiva, reset por sucesso ou expiração, separação por usuário, aquisição transacional sob concorrência e auditoria sem senhas. O cliente ASGI usa `httpx2`, conforme a integração suportada pelo Starlette atual.

Os testes estruturais do layout verificam que login e Dashboard usam assets locais, controles acessíveis, navegação prevista e arquivos estáticos públicos sem liberar páginas privadas. Playwright complementa essa camada somente nos fluxos críticos definidos na Etapa 28.

As confirmações visuais têm teste estrutural próprio: o layout autenticado inclui um único modal compartilhado, formulários protegidos usam `data-confirm`, fragmentos HTMX não usam `hx-confirm` e o JavaScript-fonte não pode chamar diálogos nativos do navegador. Regras de confirmação e CSRF continuam cobertas nas rotas do backend.

Os testes de métricas usam uma fonte determinística no lugar do host real. Eles verificam leituras atuais, cálculo da vazão de rede, reset de contadores, timestamps com timezone, expiração da janela de 15 minutos, autenticação do fragmento HTMX, integração do Chart.js local e ausência de persistência no SQLite.

Os testes da integração systemd usam executores gravadores e fakes do serviço e do processo Palworld. Eles verificam comandos e unidade exatos, timeout, `MainPID`, rejeição de nomes que possam virar opções, erros sem vazamento de stderr e seleção dos adapters somente em production. Nenhum teste executa `systemctl` real.

Os testes do health check cobrem as combinações principais dos estados `ONLINE`, `INICIANDO`, `DEGRADADO`, `OFFLINE` e `FALHA`. O transporte REST é simulado para validar Basic Auth, timeout, parsing de `/info`, autenticação rejeitada, indisponibilidade, resposta inválida e falha inesperada sem abrir rede. Testes de startup confirmam que web e worker recusam production sem os secrets obrigatórios e que não há fallback para `admin`. O fragmento autenticado do Dashboard valida o estado agregado e seus três sinais.

Os testes de ciclo de vida verificam os comandos fixos de Start, Stop e Restart, confirmação e CSRF, defaults 120/120/60 segundos, overrides operacionais, timeout exato, health final, fechamento da porta REST, auditoria, aquisição única e proteção transacional contra double-submit. Um fluxo de integração usa o fake SQLite compartilhado para confirmar que o worker conclui o job e que a web passa a exibir `ONLINE`, sem executar systemd ou acessar rede externa.

Os testes de desligamento cobrem as opções Agora/1/5/10, default operacional, avisos oficiais simulados, progresso, cancelamento antes do ponto irreversível e antecipação pelo Stop normal. A escalada valida a cadeia Stop falho → `FORCAR`/SIGTERM falho → `SIGKILL`, os comandos systemd exatos, auditoria, evento de notificação e a ausência de qualquer SIGKILL automático.

Os testes do sistema persistente de jobs cobrem migration, claim concorrente por dois workers, exclusão de execução duplicada, lease de identidade, heartbeat persistido, os quatro estados de health, comando systemd read-only com unidade fixa, maintenance lock global, liberação de lock terminal e recovery para `INTERRUPTED` sem requeue. Logs de job são validados quanto ao path relativo controlado, trecho exibível, ausência de detalhes internos e retenção de 90 dias.

Os testes de backup local cobrem o `POST /save` oficial e seu fake quando o
Palworld está `ONLINE`, além da cópia direta sem REST para backups manuais,
automáticos e preventivos quando o health confirma `OFFLINE`. Estados
ambíguos são recusados sem artefato válido. A suíte também cobre mundo completo
com `Players/` opaco, snapshot consistente do SQLite em WAL, configurações
allowlisted, redaction e exclusões proibidas. Manifest determinístico, hashes
individuais, SHA-256 externo, tar.gz válido e corrupção recebem validação
própria. Casos adversos incluem traversal, paths absolutos, symlinks, falha
antes e depois da publicação, cleanup restrito, retenção exata de 3,
preservação de arquivos externos, lock, duplicidade, cancelamento, recovery sem
retomada, agendamento às 04:00 no timezone configurado, autenticação, CSRF,
auditoria e logs seguros. A regressão web verifica o polling do job, o evento
terminal e a atualização isolada da lista de artefatos sem recarregar a página.

Os testes de Restore local verificam a precedência do SHA-256 externo, validação completa do manifest e do payload de disaster recovery, mundo e `Players/` opacos, merge determinístico dos INIs e preservação literal de valores sensíveis e parâmetros desconhecidos. Cobrem falha de safe save preventivo, INI atual ausente/inválido, espaço insuficiente e Stop falho antes de qualquer alteração; aplicação real em árvore temporária com modos mínimos; Start, health e logs críticos; falha parcial sem rollback e com backup preventivo preservado; retenção exata e arquivo externo; maintenance lock, double-submit, não cancelamento e recovery sem retomada. A integração web exige autenticação, CSRF, `RESTAURAR`, modal compartilhado e apenas enfileira o job. Os fakes de test/development não acessam Palworld, systemd ou filesystem estrutural reais.

Os testes do Google Drive usam adapter gravador e fake integral, sem rclone ou
rede. Cobrem argumentos e namespace fixos, quota válida/inválida, upload
automático somente após backup diário válido, upload manual, SHA-256 remoto,
download com validação antes da publicação local, exclusão gerenciada,
cancelamento seguro, maintenance lock, double-submit e recovery sem requeue.
Retenção mantém exatamente 10 registros remotos e testes de quota confirmam que
somente os registros próprios mais antigos são removidos, preservando objetos
externos. Falhas antes e depois da transferência verificam limpeza restrita,
preservação local, auditoria, `DRIVE_FAILED` e logs sem detalhes internos. As
rotas de status, upload, download e exclusão exigem autenticação e CSRF.

Os testes de Restore remoto conectam o fake integral do Drive ao mesmo executor
seguro do Restore local. Cobrem sucesso sem publicar cópia `LOCAL`, confirmação
literal, autenticação, CSRF, chave compartilhada contra Restore concorrente,
maintenance lock e recovery `INTERRUPTED` sem requeue. SHA-256 externo
divergente, tar.gz inválido, staging externo ou com symlink e falha anterior ao
Stop não criam backup preventivo nem alteram o mundo. Falha posterior à
aplicação exige revisão manual e preserva o backup preventivo e o remoto. Em
todos os resultados, apenas temporários reconhecidos são limpos e Manager,
secrets e saves de jogadores opacos seguem as garantias do fluxo local.

Os testes de Update validam o parser KeyValues, `buildid` local e público,
timestamp opcional, argumentos fixos do SteamCMD, limites de resposta e rejeição
de paths estruturais inválidos. A integração cobre verificação manual sem Update
automático, espaço crítico, backup pré-update e salvamento seguro, Stop/Start e
health, falha do SteamCMD sem rollback, auditoria e eventos sem secrets,
maintenance lock, double-submit, cancelamento seguro e recovery `INTERRUPTED` sem
requeue. As rotas exigem autenticação, CSRF e confirmação literal `ATUALIZAR`;
development e test usam fakes sem processos externos ou filesystem estrutural.

Os testes do Discord cobrem validação estrita do webhook oficial HTTPS, payload
allowlisted com menções desativadas, timeout, limite de resposta, classificação
segura de falhas e fake integral sem rede. A integração verifica claim atômico,
transições `PENDING`/`SENDING`/`SENT`/`FAILED`, backoff exato de 5 e 30 segundos,
limite de 3 tentativas, falha permanente sem configuração, recovery at least once
e ausência de URL, token, resposta externa ou resultado livre do job no banco e
nas mensagens.

Os testes de diagnóstico cobrem a severidade agregada, relatório copiável,
comandos read-only fixos de systemd e Tailscale, fakes sem acesso ao host,
autenticação das rotas, atualização HTMX e leitura dos últimos estados seguros
de SteamCMD, Drive e Discord. A integração também confirma que gerar o relatório
não cria auditoria, jobs ou notificações e não expõe secrets ou paths.

Os testes de logs validam os argumentos read-only e allowlisted do `journalctl`, parsing, classificação, proteção de secrets, fake completo, autenticação, histórico de 100/500/1000 linhas, filtros e ausência de persistência no SQLite. O aceite de reconexão abre o SSE com o cursor do histórico, simula nova conexão com `Last-Event-ID` e confirma que a entrega continua no evento seguinte sem repetir o último recebido.

A detecção operacional de falhas críticas é testada separadamente da
classificação visual. Regressões de Restore e Update confirmam que
`access-control-expose-headers: x-sentry-error` e o encerramento esperado por
SIGTERM/status 143 não causam falso `FAILED`, enquanto assinaturas críticas
continuam bloqueando a conclusão.

Os testes da REST API administrativa validam os campos oficiais tipados de jogadores, Basic Auth, timeout, servidor offline, indisponibilidade, autenticação, respostas inválidas e falhas inesperadas sem abrir rede nem expor detalhes internos. A integração web confirma que abrir ou reler a página não consulta jogadores, que somente o POST manual atualiza o cache em memória, que uma falha preserva o último snapshot válido e que anúncios exigem CSRF e repetição literal da mensagem. Sucesso e falha do anúncio são verificados na auditoria.

Kick, Ban e Unban têm testes dos endpoints e payloads oficiais exatos, fake sem rede, CSRF, modal compartilhado, motivo opcional apenas para Kick e motivo obrigatório para Ban/Unban. A integração verifica sucesso e falha segura tanto em `ban_history` quanto em `audit_events`, incluindo alvo, `userId`, administrador, motivo e resultado.

O editor do INI tem testes unitários do parser conservador, tipos, limites documentados, estruturas aninhadas, backup idêntico ao original, modo `0600`, substituição atômica, conflito por versão e rejeição de symlink. A integração web cobre autenticação, CSRF, fake sem filesystem real, ocultação de campos sensíveis, preservação de desconhecidos, falha segura, auditoria apenas com nomes de campos e oferta do Restart pelo modal compartilhado. A regressão do estado terminal verifica que `SUCCEEDED` substitui a ação repetível pela confirmação de Restart concluído.

Os testes de auditoria cobrem validação e redação defensiva na gravação,
retenção exata de 90 dias, duração derivada de jobs, origens administrativa,
automática e de sistema e classificação separada de cancelamento e interrupção.
A integração confirma autenticação, timezone configurado, filtros combináveis,
ordenação, paginação fixa de 50 registros, remoção de expirados, ausência de CSV
e proteção de valores sensíveis inclusive em registros anteriores.

Os testes de energia do host validam os comandos exatos e não bloqueantes de
reboot e poweroff, rejeição de ação arbitrária sem executar subprocesso e erros
sem copiar stdout ou stderr. A integração cobre autenticação, CSRF, frases
digitadas exatas, modal compartilhado, double-submit, maintenance lock,
auditoria, Stop seguro do Palworld antes da ação e bloqueio absoluto do comando
do host quando esse Stop falha. Development e test usam somente fake.

Os E2E usam Chromium headless em um estágio Docker separado, executado como
`palmanager`. O preparador aplica todas as migrations em um SQLite temporário,
cria somente o administrador fictício da suíte, desativa o agendamento diário
para evitar corridas e inicia web e worker no ambiente `test`. Os quatro cenários
seriais cobrem login/logout e redirecionamento de rota privada, Stop e Restart
pelos fakes, criação de backup seguida de Restore com `RESTAURAR` e gravação de
uma configuração reconhecida pelo modal compartilhado. Nenhum serviço, path ou
secret real é usado.

Os testes de deploy validam os artefatos sem executar systemd, Tailscale,
rclone ou filesystem estrutural reais. Eles verificam units web/worker
independentes e não-root, entrypoints distintos, loopback, journald, sandboxes,
grupo compartilhado do Palworld, helper root limitado a sete comandos, template
`systemd.path` com sete nomes exatos, ausência de Polkit/sudoers, tmpfiles e modos mínimos, `UMask=0027` nos
serviços transitórios iniciais, configuração sem secrets e package data de
templates/assets.

Os testes do deploy recorrente executam apenas `bash -n` e `--help`; nenhuma
operação privilegiada real é chamada. As demais asserções verificam paths e
comandos fechados, migração para `systemd.path` e rollback Polkit/sudoers legado, lock exclusivo, worktree
isolado, execução do gate como `palmanager`, compatibilidade Alembic, commit
anterior, recusa de jobs,
notificações e maintenance lock ativos, transient services protegidos, ausência
de rollback automático e validações independentes da web e do worker.

As regressões finais de hardening sincronizam a versão `1.0.0`, impedem
JavaScript inline nos templates, validam CSP/headers e o limite de 1 MiB dos
corpos HTTP, exigem `shell=False` e ambiente explícito em todo subprocesso,
protegem logs regulares contra symlink/FIFO, limitam senha e tar, verificam
permissões do `rclone.conf`, espaço de Restore e a retenção horária do worker.
A validação das units também mantém a exceção do SteamCMD restrita ao worker:
ABI `x86` e `MemoryDenyWriteExecute=false` não podem se propagar para a web
ou para o helper privilegiado.
Os testes do diagnóstico preservam a mesma separação: aceitam o diretório raiz
do Palworld como somente leitura na web e garantem que a confiança no checkout
root-owned seja limitada à chamada fixa que obtém o commit.
