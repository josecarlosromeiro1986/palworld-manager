---
name: testing-quality
description: Planejar, escrever e validar testes e qualidade do Palworld Manager. Usar ao alterar testes, corrigir bugs, configurar Pytest, Ruff, Mypy ou pre-commit, executar gates ou revisar cobertura.
---

# Testes e qualidade

Consulte a [estratégia de testes](../../../docs/development/testing.md) e o [estilo de código](../../../docs/development/code-style.md).

1. Escolha a camada mais barata que valide o comportamento com confiança.
2. Use testes unitários para regras, validações, estados, locks, parsers e timeouts.
3. Use testes de integração para SQLite, migrations, jobs, adapters e fluxos com filesystem controlado.
4. Use mocks ou fakes nas fronteiras externas. Development e test não dependem do Palworld, Discord, Google Drive, rclone ou systemd reais.
5. Reserve Playwright para poucos fluxos E2E críticos definidos na especificação; não cubra cada botão.
6. Adicione teste de regressão para cada bug corrigido quando razoável.
7. Mantenha testes determinísticos, isolados e sem secrets. Evite depender de relógio real, rede externa ou estado do host.
8. Execute Pytest, Ruff, Mypy e pre-commit pelos comandos definidos no repositório.
9. Use `make check` como gate quando ele existir; não afirme que funciona antes de o Makefile correspondente ser implementado.
10. Relate comandos executados, resultados e qualquer validação que não pôde ser realizada.
