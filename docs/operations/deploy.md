# Deploy

> Status: Planejado para a V1.

O deploy manual seguirá esta ordem:

```text
atualizar código
→ dependências
→ build frontend
→ migrations
→ validação
→ restart dos dois serviços
→ validar web
→ validar worker
```

Um futuro `deploy.sh` deverá registrar o commit anterior, validar configuração e migrations sem revelar secrets e reiniciar `palworld-manager.service` e `palworld-manager-worker.service`.

A validação será separada:

```text
palworld-manager.service
→ systemd ativo + /health

palworld-manager-worker.service
→ systemd ativo + heartbeat SQLite < 30 segundos
```

O worker atualizará o heartbeat a cada 10 segundos e não terá endpoint HTTP próprio. Após o restart, serviço ativo sem heartbeat ficará `STARTING` durante os primeiros 30 segundos; ao atingir 30 segundos sem heartbeat ficará `UNRESPONSIVE`. Um heartbeat com menos de 30 segundos confirma `HEALTHY`; serviço inativo é `OFFLINE`.

`deploy.sh` ainda não existe. As etapas e comandos definitivos só serão documentados depois que o script e as unidades de produção forem implementados e testados.

O rollback do Manager será manual para o commit anterior, seguido pelas validações compatíveis com a versão escolhida. Não haverá rollback automático. Como migrations podem limitar o retorno a versões antigas, o procedimento definitivo deverá declarar explicitamente a compatibilidade e os pré-requisitos antes de qualquer mudança.

Veja a [instalação em produção](production-install.md) e [SPECIFICATION.md](../../SPECIFICATION.md).
