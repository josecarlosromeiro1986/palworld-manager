# Instruções para agentes

## Fontes e escopo

- Leia [SPECIFICATION.md](SPECIFICATION.md) antes de mudanças relevantes; ela é a fonte de verdade da V1.
- Use [README.md](README.md) como entrada rápida e [docs/index.md](docs/index.md) para documentação técnica e operacional.
- Inspecione o código e as ferramentas existentes antes de modificar. Não invente comandos nem declare como implementado o que ainda é planejado.

## Continuidade entre sessões

Antes de iniciar ou continuar uma etapa:

1. Inspecione `git status` e alterações não commitadas.
2. Leia este arquivo e consulte `SPECIFICATION.md`.
3. Consulte [docs/development/progress.md](docs/development/progress.md) para identificar a última etapa concluída e a próxima.
4. Verifique commits recentes quando necessário para entender o trabalho existente.
5. Carregue somente as skills relevantes para a tarefa.
6. Não repita etapas concluídas nem sobrescreva trabalho sem entender sua finalidade.

O estado do repositório e o histórico Git prevalecem sobre suposições baseadas em conversas anteriores. Se essas fontes entrarem em conflito, esclareça o conflito antes de implementar.

## Decisões não especificadas

Quando uma decisão de produto, arquitetura, segurança ou comportamento não estiver definida na especificação e bloquear a implementação:

1. Não assuma nem escolha silenciosamente uma alternativa apenas para continuar.
2. Interrompa somente o ponto dependente da decisão e explique o problema.
3. Apresente alternativas relevantes, seus impactos e uma recomendação quando aplicável.
4. Faça uma pergunta por vez e aguarde a decisão humana.
5. Depois da decisão, atualize `SPECIFICATION.md` e os documentos relacionados antes de continuar.

Não interrompa por detalhes triviais resolvidos pelas convenções já estabelecidas.

## Arquitetura e segurança

- Preserve módulos por domínio e separe UI, regras de negócio e adapters de integração.
- Mantenha web e worker separados. SQLite é banco, coordenação e fila da V1; não introduza Redis, Celery, RabbitMQ ou Kafka sem nova decisão arquitetural.
- Nunca execute a aplicação como `root`, conceda `sudo ALL` ou inclua secrets em código, testes, fixtures, logs, documentação ou commits.
- Não use `shell=True` com input externo. Valide argumentos, serviços e paths antes de executar comandos.
- Aplique privilégio mínimo. Trate backup, restore, update, systemd, SteamCMD e operações privilegiadas como áreas sensíveis e exija as confirmações especificadas.
- Preserve parâmetros desconhecidos do `PalWorldSettings.ini`.
- Use somente interfaces oficiais suportadas do Palworld. Não invente endpoints nem leia/manipule saves para descobrir jogadores offline.
- Não retome automaticamente Restore ou Update interrompido quando o estado for ambíguo.
- Nunca exclua arquivos externos à área administrada pelo Palworld Manager.

## Execução das etapas

Trabalhe **uma etapa por vez**. Não antecipe funcionalidades de etapas futuras sem necessidade para os critérios de aceite atuais.

```text
inspecionar
→ planejar
→ implementar
→ testar
→ corrigir
→ validar critérios de aceite
→ atualizar documentação
→ make check
→ revisar diff
→ commit
→ parar
```

- Atualize testes junto com as mudanças e adicione regressão para bugs quando razoável.
- Atualize a documentação afetada e o status de implementação somente depois de os critérios de aceite passarem.
- Atualize `docs/development/progress.md` apenas ao concluir uma etapa.
- Rode `make check` quando o comando existir; até lá, execute somente validações definidas no repositório.
- Antes do commit, confirme testes e aceite, revise `git diff` e `git status` e verifique documentação e ausência de secrets.
- Mantenha `1 etapa concluída = 1 commit`. Não faça commit de etapa sabidamente quebrada.
- Após o commit, apresente o resumo, pare e aguarde nova instrução. Não inicie automaticamente a etapa seguinte.
- Pare e peça decisão humana diante de informação real não simulável ou operação destrutiva em estado incerto.

## Skills do projeto

- `$project-conventions`: desenvolvimento geral e organização do código.
- `$testing-quality`: testes, lint, tipos e gate de qualidade.
- `$palworld-integration`: API do Palworld, jogadores, ações, health, SteamCMD e INI.
- `$privileged-operations`: systemd, sudoers, processos, host e filesystem protegido.
- `$jobs-worker`: fila SQLite, worker, locks, heartbeat e notificações externas.
- `$backup-restore`: criação, retenção e restauração de backups locais ou remotos.
- `$documentation`: README, especificação, docs e ADRs.

Use todas as skills relevantes quando uma mudança atravessar mais de uma dessas áreas.
