# Energia do host Ubuntu

> Status: Implementado na Etapa 27; instalação das regras de sudoers permanece planejada para a Etapa 29.

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
produz exatamente um dos comandos:

```text
/usr/bin/sudo --non-interactive /usr/bin/systemctl --no-block reboot
/usr/bin/sudo --non-interactive /usr/bin/systemctl --no-block poweroff
```

Os argumentos não recebem texto da requisição, `shell=False` é obrigatório e
stdout/stderr não são copiados para UI, logs ou auditoria. Development e test
usam um fake em memória e nunca controlam o host real.

A Etapa 27 não instala sudoers. O deploy da Etapa 29 deverá liberar somente os
comandos exatos necessários para `palmanager`, junto das regras fechadas já
previstas para o Palworld; `sudo ALL` continua proibido.

Consulte [Segurança](../architecture/security.md),
[Jobs e locks](../architecture/jobs-and-locks.md) e
[SPECIFICATION.md](../../SPECIFICATION.md) para os requisitos oficiais.
