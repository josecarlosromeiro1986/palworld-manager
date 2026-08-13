---
name: documentation
description: Criar ou atualizar documentação do Palworld Manager. Usar em SPECIFICATION.md, README.md, docs, ADRs, links, status de implementação ou mudanças de comportamento documentado.
---

# Documentação

1. Trate [SPECIFICATION.md](../../../SPECIFICATION.md) como fonte de verdade dos requisitos da V1.
2. Mantenha [README.md](../../../README.md) como entrada rápida e [docs/index.md](../../../docs/index.md) como índice técnico e operacional.
3. Explique organização e operação nos documentos temáticos; não copie grandes blocos da especificação.
4. Use somente links relativos dentro do repositório e valide todos os links alterados.
5. Distinga explicitamente `Planejado`, `Em desenvolvimento` e `Implementado`.
6. Nunca documente uma funcionalidade planejada como disponível nem invente comandos, scripts, endpoints ou procedimentos não validados.
7. Atualize documentos afetados quando comportamento implementado, arquitetura ou operação mudar.
8. Nunca inclua senhas, tokens, webhooks, cookies, credenciais, chaves ou conteúdo de arquivos de secrets.
9. Crie ADRs somente para decisões arquiteturais relevantes que precisem de justificativa histórica; não use ADR para toda regra existente.
10. Em caso de conflito, corrija os documentos derivados para corresponder à especificação. Se a própria especificação for ambígua, pare e peça decisão humana.
