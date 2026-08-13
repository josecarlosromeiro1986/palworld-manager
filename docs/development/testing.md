# Testes

> Status: Planejado para a V1.

Pytest será a base da suíte automatizada.

- **Unitários:** regras de negócio, validações, estados de health, locks, retenção, autenticação, cancelamento e parsing.
- **Integração:** SQLite, migrations, jobs, backups e adapters contra serviços simulados.
- **E2E:** poucos fluxos críticos no navegador com Playwright, como autenticação e confirmações destrutivas essenciais.

Playwright não deve cobrir cada botão. A maior parte do comportamento deve ser validada por testes rápidos e determinísticos abaixo da camada de interface.

O objetivo futuro é que:

```bash
make check
```

execute o gate completo de qualidade antes de commit e deploy. Esse comando ainda não existe no repositório. Quando for implementado, esta página deverá listar exatamente as verificações reais e como executar testes específicos.
