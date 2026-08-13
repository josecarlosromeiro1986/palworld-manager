# Instruções para agentes

## Fontes e escopo

- Leia [SPECIFICATION.md](SPECIFICATION.md) antes de mudanças relevantes; ela é a fonte de verdade da V1.
- Use [README.md](README.md) como entrada rápida e [docs/index.md](docs/index.md) para documentação técnica e operacional.
- Inspecione o código e as ferramentas existentes antes de modificar. Não invente comandos nem declare como implementado o que ainda é planejado.
- Trabalhe uma etapa da especificação por vez. Não antecipe etapas futuras sem necessidade para o aceite atual.

## Arquitetura e segurança

- Preserve módulos por domínio e separe UI, regras de negócio e adapters de integração.
- Mantenha web e worker separados. SQLite é banco, coordenação e fila da V1; não introduza Redis, Celery, RabbitMQ ou Kafka sem nova decisão arquitetural.
- Nunca execute a aplicação como `root`, conceda `sudo ALL` ou inclua secrets em código, testes, fixtures, logs, documentação ou commits.
- Não use `shell=True` com input externo. Valide argumentos, serviços e paths antes de executar comandos.
- Trate backup, restore, update, systemd, SteamCMD e operações privilegiadas como áreas sensíveis.
- Preserve parâmetros desconhecidos do `PalWorldSettings.ini`.
- Use somente interfaces oficiais suportadas do Palworld. Não invente endpoints nem leia/manipule saves para descobrir jogadores offline.

## Entrega

- Atualize testes junto com mudanças e adicione regressão para bugs quando razoável.
- Atualize a documentação quando o comportamento ou o estado de implementação mudar.
- Rode `make check` quando o comando existir. Até lá, execute somente validações realmente definidas no repositório.
- Faça um commit coerente por etapa somente depois de testes e critérios de aceite passarem.
- Pare e peça decisão humana diante de ambiguidade arquitetural, informação real não simulável ou operação destrutiva em estado incerto.

## Skills do projeto

- `$project-conventions`: desenvolvimento geral e organização do código.
- `$testing-quality`: testes, lint, tipos e gate de qualidade.
- `$palworld-integration`: API do Palworld, jogadores, ações, health, SteamCMD e INI.
- `$privileged-operations`: systemd, sudoers, processos, host e filesystem protegido.
- `$jobs-worker`: fila SQLite, worker, locks, heartbeat e notificações externas.
- `$backup-restore`: criação, retenção e restauração de backups locais ou remotos.
- `$documentation`: README, especificação, docs e ADRs.

Use todas as skills relevantes quando uma mudança atravessar mais de uma dessas áreas.
