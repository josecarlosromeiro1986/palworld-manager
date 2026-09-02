---
name: project-conventions
description: Aplicar as convenções gerais de desenvolvimento do Palworld Manager. Usar ao implementar ou refatorar backend, frontend server-side, configuração, persistência ou organização por domínio.
---

# Convenções do projeto

Consulte [SPECIFICATION.md](../../../SPECIFICATION.md), a [visão geral](../../../docs/architecture/overview.md) e o [estilo de código](../../../docs/development/code-style.md).

1. Inspecione o domínio e os padrões existentes antes de editar.
2. Mantenha o backend modular por domínio; evite concentrar regras em rotas ou em `main.py`.
3. Separe UI, serviços/regras de negócio e adapters de integração.
4. Mantenha rotas FastAPI finas. Use Jinja2 para renderização server-side e HTMX para formulários e fragmentos conforme a especificação.
5. Use SQLAlchemy para persistência e Alembic para toda mudança de schema.
6. Modele configuração com Pydantic Settings e valide valores no startup.
7. Persista timestamps em UTC e converta para o timezone configurado apenas na apresentação.
8. Escreva interface e documentação em Português (Brasil).
9. Use type hints nas interfaces e regras relevantes. Prefira funções simples, pequenas e testáveis quando isso melhorar clareza.
10. Evite abstrações prematuras. Adicione uma abstração somente quando reduzir complexidade real ou seguir um padrão já estabelecido.
11. Não adicione dependências sem necessidade demonstrável, compatibilidade verificada e justificativa na mudança.
12. Atualize testes e documentação afetados; execute apenas os comandos de validação existentes no repositório.
