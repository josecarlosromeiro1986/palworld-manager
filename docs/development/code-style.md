# Estilo de código

> Status: Planejado para a V1.

## Ferramentas e práticas

- Ruff para lint e formatação.
- Mypy para análise estática.
- Pytest para testes automatizados.
- pre-commit para verificações locais reprodutíveis.
- Type hints nas interfaces e regras relevantes.
- Organização modular por domínio, com funções pequenas quando isso melhorar clareza e teste.
- Separação entre UI, regras de negócio e integrações.
- Adapters e interfaces para serviços externos, substituíveis por fakes em development e test.
- Execução de comandos com listas de argumentos, validação explícita e `shell=False`.

Evite concentrar regras em views ou em um `main.py` grande. Código que manipula caminhos, processos, permissões ou arquivos de backup exige testes dos casos adversos.

## Fluxo por etapa

```text
uma etapa
→ implementação
→ testes
→ make check
→ documentação
→ commit
```

O fluxo passa a ser executável após a criação das ferramentas correspondentes. Até lá, não se deve tratar `make check` como disponível. Consulte o plano incremental em [SPECIFICATION.md](../../SPECIFICATION.md).
