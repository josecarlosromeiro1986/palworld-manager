# Estilo de código

> Status: Implementado para a V1 `1.0.0`; Ruff, Mypy, Pytest, frontend check e pre-commit compõem o gate reprodutível.

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

## Confirmações na interface

O componente compartilhado `templates/components/confirmation_modal.html`, incluído pelo layout autenticado, é o padrão para toda confirmação visual. Não use `window.alert`, `window.confirm`, `window.prompt` ou `hx-confirm`; mensagens informativas e erros que não exigem escolha permanecem inline com semântica acessível.

Formulários que exigem confirmação declaram `data-confirm` e configuram o conteúdo sem JavaScript específico da página:

```html
<form
  data-confirm
  data-confirm-title="Reiniciar servidor?"
  data-confirm-message="Reiniciar o servidor Palworld?"
  data-confirm-button="Reiniciar"
  data-confirm-tone="warning"
>
```

`data-confirm-description` altera a explicação, `data-confirm-preview-label` altera o rótulo do conteúdo revisado e `data-confirm-source` aponta para o `id` de um input ou textarea cujo valor deve aparecer no modal. Os tons aceitos pelo padrão são `default`, `warning` e `danger`. O controlador usa delegação de evento para abranger fragmentos HTMX inseridos depois do carregamento e preserva o botão que originou o submit.

Formulários dentro de fragmentos HTMX que se atualizam enquanto o modal pode estar aberto também devem declarar um `data-confirm-key` estável e único. O controlador usa essa chave para localizar a versão atual do formulário depois de um swap e restaura os valores capturados antes de enviar a ação confirmada:

```html
<form data-confirm data-confirm-key="shutdown-now-123" hx-post="/acao">
```

O modal é apenas a confirmação visual. CSRF, palavras digitadas, valores exatos e demais regras de segurança continuam validados no backend.

## Fluxo por etapa

```text
uma etapa
→ implementação
→ testes
→ make check
→ documentação
→ commit
```

O fluxo base é executável por `make check`. Use `make format` para formatar e `make precommit` para executar os hooks sobre os arquivos rastreados. Consulte o plano incremental em [SPECIFICATION.md](../../SPECIFICATION.md).
