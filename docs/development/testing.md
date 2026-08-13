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

Os testes de métricas usam uma fonte determinística no lugar do host real. Eles verificam leituras atuais, cálculo da vazão de rede, reset de contadores, timestamps com timezone, expiração da janela de 15 minutos, autenticação do fragmento HTMX, integração do Chart.js local e ausência de persistência no SQLite.

Os testes da integração systemd usam um executor gravador e o fake do serviço Palworld. Eles verificam comando e unidade exatos, timeout, rejeição de nomes que possam virar opções, erros sem vazamento de stderr, seleção do adapter somente em production e consulta autenticada dos estados ativo/inativo no Dashboard. Nenhum teste executa `systemctl` real.

`make e2e` está reservado e apenas informa que os testes de navegador serão adicionados na Etapa 28; ele não representa cobertura E2E implementada nesta fase.
