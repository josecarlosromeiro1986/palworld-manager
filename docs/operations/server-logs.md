# Logs do servidor Palworld

> Status: Implementado.

A página **Logs** consulta somente o journal da unidade estrutural `PALWORLD_SERVICE`. Os registros não são copiados para o SQLite: o histórico e o streaming continuam tendo o journald como fonte de verdade.

## Histórico e visualização

O administrador autenticado pode carregar as 100, 500 ou 1000 linhas mais recentes. A interface preserva a mensagem recebida, exceto pela proteção obrigatória de valores sensíveis, e permite:

- pesquisar no texto exibido;
- filtrar por erro, aviso, conexão, sistema ou normal;
- pausar e retomar a inclusão visual de novos eventos;
- habilitar ou desabilitar rolagem automática;
- copiar o trecho atualmente visível após os filtros.

O navegador insere mensagens com `textContent`; conteúdo do journal não é interpretado como HTML.

## Streaming e reconexão

Cada evento SSE usa o cursor original do journald no campo `id`. A primeira conexão parte do cursor da última linha do histórico. Em uma reconexão, o navegador envia `Last-Event-ID` e o backend reinicia o `journalctl` com `--after-cursor=<cursor>`, retomando estritamente depois do último evento entregue. O endpoint também envia keepalive e instrui o cliente a tentar novamente após dois segundos.

O cliente usa a reconexão nativa de `EventSource` e mostra o estado **Reconectando ao streaming…** enquanto a conexão não volta. Pausar a tela não encerra o SSE; os eventos ficam em um buffer limitado e são incluídos ao retomar.

## Segurança e ambientes

Em produção, o adapter executa somente `/usr/bin/journalctl`, com lista fixa de argumentos, `shell=False`, unidade previamente validada, limites fechados e campos mínimos. O cursor é validado antes de virar o valor de `--after-cursor`; stderr e detalhes internos do host não chegam à interface. A integração é somente leitura e não usa `sudo`.

Credenciais estruturais conhecidas e atribuições comuns como `password=`, `token=` ou `authorization=` são substituídas por `[SEGREDO PROTEGIDO]` antes da entrega. Essa proteção tem precedência sobre a preservação literal do texto.

Development e test usam um fake completo e determinístico, com histórico, categorias, streaming e cursores retomáveis, sem consultar o journal ou controlar o host. A permissão read-only necessária para o usuário `palmanager` ler o journal em produção será instalada e validada na etapa de deploy.
