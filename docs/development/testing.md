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

`make check` executa Ruff, verificação de formatação, Mypy e Pytest dentro do container. A configuração de pre-commit pode ser executada com `make precommit`.

Os testes de configuração isolam variáveis de ambiente e arquivos locais. A suíte de integração também verifica que uma configuração inválida impede o startup sem reproduzir o valor recebido na saída de erro.

`make e2e` está reservado e apenas informa que os testes de navegador serão adicionados na Etapa 28; ele não representa cobertura E2E implementada nesta fase.
