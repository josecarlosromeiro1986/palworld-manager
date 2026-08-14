# Testes

> Status: Em desenvolvimento. Pytest e o gate base estão implementados; a suíte crescerá por etapa.

Pytest será a base da suíte automatizada.

- **Unitários:** regras de negócio, validações, estados de health, locks, retenção, autenticação, cancelamento e parsing.
- **Integração:** SQLite, migrations, jobs, backups e adapters contra serviços simulados.
- **E2E:** poucos fluxos críticos no navegador com Playwright, como autenticação e confirmações destrutivas essenciais.

Playwright não deve cobrir cada botão. A maior parte do comportamento deve ser validada por testes rápidos e determinísticos abaixo da camada de interface.

Comandos disponíveis:

```bash
make test
make check
```

`make check` recompila e valida os assets de frontend, executa Ruff, verificação de formatação, Mypy e Pytest dentro do container. A configuração de pre-commit pode ser executada com `make precommit` e inclui o mesmo gate de frontend.

Os testes de configuração isolam variáveis de ambiente e arquivos locais. A suíte de integração também verifica que uma configuração inválida impede o startup sem reproduzir o valor recebido na saída de erro.

Testes de banco usam um `manager.db` temporário por caso e executam `alembic upgrade head`. Eles verificam schema, revisão, pragmas SQLite, integridade referencial e commit/rollback sem tocar no volume de desenvolvimento.

Os testes de credenciais verificam o formato Argon2id, a senha mínima, rejeição de hashes inválidos, criação de um único administrador e redefinição pela CLI. Usam bancos temporários e confirmam que senhas em texto puro não são persistidas nem exibidas.

Os testes de autenticação cobrem rotas privadas por padrão, login e logout, CSRF, atributos dos cookies, revogação por troca de senha e limites exatos de 8 horas totais e 1 hora de inatividade. Também verificam o bloqueio na quinta falha consecutiva, reset por sucesso ou expiração, separação por usuário, aquisição transacional sob concorrência e auditoria sem senhas. O cliente ASGI usa `httpx2`, conforme a integração suportada pelo Starlette atual.

Os testes estruturais do layout verificam que login e Dashboard usam assets locais, controles acessíveis, navegação prevista e arquivos estáticos públicos sem liberar páginas privadas. A inspeção visual automatizada com Playwright permanece reservada aos fluxos E2E críticos da Etapa 28.

As confirmações visuais têm teste estrutural próprio: o layout autenticado inclui um único modal compartilhado, formulários protegidos usam `data-confirm`, fragmentos HTMX não usam `hx-confirm` e o JavaScript-fonte não pode chamar diálogos nativos do navegador. Regras de confirmação e CSRF continuam cobertas nas rotas do backend.

Os testes de métricas usam uma fonte determinística no lugar do host real. Eles verificam leituras atuais, cálculo da vazão de rede, reset de contadores, timestamps com timezone, expiração da janela de 15 minutos, autenticação do fragmento HTMX, integração do Chart.js local e ausência de persistência no SQLite.

Os testes da integração systemd usam executores gravadores e fakes do serviço e do processo Palworld. Eles verificam comandos e unidade exatos, timeout, `MainPID`, rejeição de nomes que possam virar opções, erros sem vazamento de stderr e seleção dos adapters somente em production. Nenhum teste executa `systemctl` real.

Os testes do health check cobrem as combinações principais dos estados `ONLINE`, `INICIANDO`, `DEGRADADO`, `OFFLINE` e `FALHA`. O transporte REST é simulado para validar Basic Auth, timeout, parsing de `/info`, autenticação rejeitada, indisponibilidade, resposta inválida e falha inesperada sem abrir rede. Testes de startup confirmam que web e worker recusam production sem os secrets obrigatórios e que não há fallback para `admin`. O fragmento autenticado do Dashboard valida o estado agregado e seus três sinais.

Os testes de ciclo de vida verificam os comandos fixos de Start, Stop e Restart, confirmação e CSRF, defaults 120/120/60 segundos, overrides operacionais, timeout exato, health final, fechamento da porta REST, auditoria, aquisição única e proteção transacional contra double-submit. Um fluxo de integração usa o fake SQLite compartilhado para confirmar que o worker conclui o job e que a web passa a exibir `ONLINE`, sem executar systemd ou acessar rede externa.

Os testes de desligamento cobrem as opções Agora/1/5/10, default operacional, avisos oficiais simulados, progresso, cancelamento antes do ponto irreversível e antecipação pelo Stop normal. A escalada valida a cadeia Stop falho → `FORCAR`/SIGTERM falho → `SIGKILL`, os comandos systemd exatos, auditoria, evento de notificação e a ausência de qualquer SIGKILL automático.

Os testes de logs validam os argumentos read-only e allowlisted do `journalctl`, parsing, classificação, proteção de secrets, fake completo, autenticação, histórico de 100/500/1000 linhas, filtros e ausência de persistência no SQLite. O aceite de reconexão abre o SSE com o cursor do histórico, simula nova conexão com `Last-Event-ID` e confirma que a entrega continua no evento seguinte sem repetir o último recebido.

Os testes da REST API administrativa validam os campos oficiais tipados de jogadores, Basic Auth, timeout, servidor offline, indisponibilidade, autenticação, respostas inválidas e falhas inesperadas sem abrir rede nem expor detalhes internos. A integração web confirma que abrir ou reler a página não consulta jogadores, que somente o POST manual atualiza o cache em memória, que uma falha preserva o último snapshot válido e que anúncios exigem CSRF e repetição literal da mensagem. Sucesso e falha do anúncio são verificados na auditoria.

`make e2e` está reservado e apenas informa que os testes de navegador serão adicionados na Etapa 28; ele não representa cobertura E2E implementada nesta fase.
