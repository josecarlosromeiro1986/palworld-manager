# REST API do Palworld

> Status: Planejado para a V1.

A integração usará exclusivamente a REST API oficial do Palworld para:

- consultar jogadores online sob demanda, sem polling contínuo;
- enviar anúncios;
- executar Kick, Ban e Unban;
- contribuir para health checks quando aplicável.

O cliente deverá tipar respostas, aplicar timeout, tratar autenticação e distinguir servidor offline, API indisponível, resposta inválida e falha inesperada. Development e test usarão uma implementação simulada.

> O Palworld Manager não tentará descobrir jogadores offline lendo/modificando saves na V1.

Nenhum endpoint é documentado nesta fase. Ao implementar a integração, cada endpoint e comportamento deverá ser confirmado na documentação oficial vigente antes de entrar neste documento. Veja os requisitos de jogadores e administração em [SPECIFICATION.md](../../SPECIFICATION.md).
