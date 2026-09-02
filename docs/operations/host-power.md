# Energia do host Ubuntu

> Status: Implementado na Etapa 27; fronteira mínima systemd entregue na Etapa 29 e revisada para gatilhos `systemd.path`.

O Dashboard autenticado oferece **Reiniciar Ubuntu** e **Desligar Ubuntu**. As
duas ações exigem sessão válida, CSRF, o modal compartilhado e a frase exata
`REINICIAR UBUNTU` ou `DESLIGAR UBUNTU`. A interface avisa que web e worker
ficarão indisponíveis durante o reinício ou após o desligamento.

## Fluxo seguro

A web apenas cria um job persistente `HOST_REBOOT` ou `HOST_SHUTDOWN`. O worker:

1. adquire o maintenance lock global;
2. consulta o health compartilhado do Palworld;
3. quando necessário, executa o Stop assistido imediato e exige resultado
   saudável `OFFLINE`;
4. somente depois solicita a ação de energia ao systemd;
5. registra resultado, duração, log controlado e auditoria.

Se a comunicação ou o Stop do Palworld falhar, nenhum comando de energia é
executado. O job não é cancelável. Uma execução interrompida fica
`INTERRUPTED`, exige revisão manual e nunca é retomada automaticamente.

## Privilégio mínimo

Em production, o adapter aceita apenas a enum fechada `REBOOT`/`SHUTDOWN` e
cria exatamente um dos pedidos vazios e exclusivos:

```text
/run/palworld-manager/host-control/host-reboot.request
/run/palworld-manager/host-control/host-poweroff.request
```

O nome não recebe texto da requisição e nenhum subprocesso é iniciado pelo
adapter. Development e test usam um fake em memória e nunca controlam o host
real.

O template `systemd.path` observa somente cada nome exato e aciona o `oneshot`
correspondente; os dois branches de energia traduzem para `systemctl --no-block reboot`
e `poweroff`. Não existe grant Polkit ou autorização para SteamCMD,
rclone ou serviço arbitrário.

Consulte [Segurança](../architecture/security.md),
[Jobs e locks](../architecture/jobs-and-locks.md) e
[SPECIFICATION.md](../../SPECIFICATION.md) para os requisitos oficiais.
