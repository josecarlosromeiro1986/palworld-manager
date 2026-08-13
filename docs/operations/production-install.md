# Instalação em produção

> Status: Planejado para a V1.

A produção será uma instalação nativa em Ubuntu Server, sem Docker:

```text
Ubuntu Server
+ venv
+ systemd
+ Tailscale Serve
```

Identificadores planejados:

```text
Usuário:         palmanager
Aplicação:       /opt/palworld-manager
Banco SQLite:    /var/lib/palworld-manager/manager.db
Serviço web:     palworld-manager.service
Serviço worker:  palworld-manager-worker.service
Secrets:         /etc/palworld-manager/secrets.env
```

Os serviços web e worker serão processos independentes configurados via systemd. Ambos serão executados pelo usuário `palmanager`, nunca como `root`, usarão a mesma configuração estrutural apropriada e acessarão o mesmo banco SQLite quando necessário.

`palworld-manager.service` executará o FastAPI e escutará somente em `127.0.0.1`. Seu `/health` verificará exclusivamente a aplicação web. `palworld-manager-worker.service` consumirá os jobs persistidos, executará as operações demoradas ou críticas e será o único processo autorizado a entregar notificações externas.

O worker não terá servidor HTTP. Ele atualizará um heartbeat no SQLite a cada 10 segundos; sua saúde combinará o heartbeat com o estado e o tempo de ativação do serviço no systemd. Serviço ativo sem heartbeat fica `STARTING` com menos de 30 segundos desde a ativação e `UNRESPONSIVE` a partir de 30 segundos. Heartbeat inferior a 30 segundos com serviço ativo significa `HEALTHY`, heartbeat de 30 segundos ou mais significa `UNRESPONSIVE`, e serviço inativo significa `OFFLINE`.

Tailscale Serve fornecerá acesso privado com HTTPS apenas ao serviço web; journald receberá os logs de ambos.

Operações privilegiadas continuarão limitadas por regras mínimas de `sudoers`, com comandos e argumentos validados.

Node.js e npm serão necessários apenas para o build de assets, não como serviço de produção. Permissões, unidades systemd, `sudoers`, scripts e configuração do Tailscale ainda serão implementados e validados na etapa de deploy; por isso, este documento não é um tutorial executável.

Consulte [Segurança](../architecture/security.md) e os requisitos completos em [SPECIFICATION.md](../../SPECIFICATION.md).
