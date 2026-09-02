# ADR-0001 — Controle privilegiado por systemd.path

## Status

Aceito

## Contexto

Web e worker precisam permanecer como `palmanager` com `NoNewPrivileges=true`,
mas sete ações fechadas exigem root: Start, Stop, Restart, SIGTERM e SIGKILL do
`palworld.service`, além de reboot e poweroff. A primeira implementação usava
Polkit para autorizar somente as instâncias helper. No Ubuntu 22.04, o backend
Local Authority em uso não avaliou a regra JavaScript instalada. O formato
`.pkla` disponível não filtra os detalhes `unit` e `verb`; autorizar
`org.freedesktop.systemd1.manage-units` nesse formato ampliaria o acesso para
serviços arbitrários.

## Decisão

O worker cria um arquivo vazio e exclusivo para uma enum de sete nomes sob
`/run/palworld-manager/host-control`. O diretório é criado por tmpfiles como
`root:palmanager 0770`; a escrita é concedida somente ao worker por
`ReadWritePaths`. O adapter abre o diretório sem seguir symlink e valida tipo,
owner, grupo e modo antes de criar cada arquivo como `0600`.

Sete instâncias exatas do template `systemd.path` observam esses arquivos e
acionam uma unit `oneshot` root. O helper valida o pedido, remove-o antes da
ação e traduz a instância para um único comando fixo. A aplicação não recebe
sudo nem grant Polkit, não inicia units diretamente e não aceita serviço,
argumento ou path livre.

## Consequências

- `NoNewPrivileges=true` e `RestrictSUIDSGID=true` permanecem ativos no worker.
- A superfície privilegiada continua limitada às sete ações documentadas.
- As sete instâncias `.path` precisam estar habilitadas e ativas em produção.
- `/run` é volátil; tmpfiles recria o diretório a cada boot antes dos paths.
- Um pedido já existente causa falha controlada, sem sobrescrita ou retomada
  ambígua.
- O deploy remove a regra Polkit atual, mas mantém suporte explícito a artefatos
  Polkit e sudoers apenas para rollback de commits anteriores.
