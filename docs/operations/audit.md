# Histórico e auditoria

> Status: Implementado na Etapa 26.

A página autenticada **Histórico / Auditoria** apresenta a trilha persistente das
ações administrativas, automáticas e internas do Palworld Manager. A consulta é
somente leitura e não cria jobs nem executa integrações externas.

Cada evento registra, quando aplicável:

- data e hora em UTC, exibidas no timezone configurado no Painel;
- ação e resultado;
- origem `Administrador`, `Automático` ou `Sistema`;
- usuário, alvo, motivo e detalhes seguros;
- duração e job relacionado.

Os resultados possíveis são `Sucesso`, `Falha`, `Cancelado` e `Interrompido`.
Jobs encontrados em execução após a perda do lease do worker são classificados
como interrompidos, sem retomada automática. Eventos associados a jobs concluídos
recebem a duração calculada pelos timestamps persistidos.

## Consulta e retenção

Os filtros de período, ação, resultado, origem, usuário e alvo podem ser
combinados. A ordenação mostra os eventos mais recentes primeiro, com paginação
fixa de 50 registros. Exportação CSV não faz parte da V1.

Eventos com mais de 90 dias são removidos durante as operações normais de
gravação e consulta da auditoria. A retenção afeta somente `audit_events`; não
remove jobs, logs ou registros de backup relacionados.

## Proteção de dados

Senhas, tokens, webhooks, cookies, credenciais, headers, respostas externas e
erros livres não podem entrar na auditoria. O serviço aplica redação defensiva a
campos textuais e detalhes estruturados antes de persistir novos eventos. A
consulta também mascara valores estruturais sensíveis configurados no ambiente,
protegendo a interface inclusive ao exibir registros anteriores.

Detalhes são limitados em tamanho, profundidade e quantidade de itens. Integrações
e operações registram categorias seguras de resultado, nunca saídas brutas.

Consulte [Segurança](../architecture/security.md) para as regras gerais de
secrets, [Jobs e locks](../architecture/jobs-and-locks.md) para recuperação do
worker e [SPECIFICATION.md](../../SPECIFICATION.md) para os requisitos oficiais.
