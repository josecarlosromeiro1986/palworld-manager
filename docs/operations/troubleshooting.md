# Troubleshooting

> Status: Em desenvolvimento. A tela de Diagnóstico e os procedimentos seguros
> de validação da instalação de produção estão implementados; casos descobertos
> em operação real continuarão ampliando este documento.

Este documento é um índice inicial e deverá crescer a partir de problemas reais e procedimentos validados. Enquanto os componentes não estiverem implementados, não há comandos de correção suportados para registrar.

Antes de qualquer intervenção futura, preservar logs e contexto, evitar alterações destrutivas e nunca incluir secrets no material de diagnóstico.

## Palworld Manager não inicia

1. Valide separadamente as units e preserve a saída sem variáveis de ambiente:

   ```bash
   systemctl status palworld-manager.service --no-pager
   systemctl status palworld-manager-worker.service --no-pager
   journalctl --unit palworld-manager.service --unit palworld-manager-worker.service --lines 100 --no-pager
   ```

2. Confirme owner e modo sem imprimir conteúdo:

   ```bash
   stat -c '%U %G %a %n' /etc/palworld-manager/manager.env /etc/palworld-manager/secrets.env
   stat -c '%U %G %a %n' /var/lib/palworld-manager /var/lib/palworld-manager/manager.db
   ```

3. Execute `systemd-analyze verify` nas duas units instaladas. Corrija primeiro
   configuração ausente, permissão, migration ou asset; não execute os serviços
   como root para contornar a falha.

## Palworld não inicia

Consulte `systemctl status palworld.service` e
`journalctl --unit palworld.service --lines 100 --no-pager`. Confirme que o
drop-in de produção contém somente `SupplementaryGroups=palworld-manager` e
`UMask=0007`, que o diretório não é symlink e que o processo mantém seu usuário
original. Não altere `ExecStart` pelo Manager.

## REST API indisponível

Use a tela **Diagnóstico** e o health compartilhado. Confirme serviço, processo,
porta local e configuração oficial da REST API. Não coloque Basic Auth em URL ou
linha de comando e não copie username/password para logs.

## SteamCMD

1. Consulte o job na página **Atualizações** e confirme a categoria pública da
   falha; a saída bruta do SteamCMD não é exposta pela UI nem persistida.
2. Verifique se o worker não-root consegue executar o path estrutural `STEAMCMD`
   e ler/escrever somente a instalação em `PALWORLD_DIR`.
3. Confirme a presença de
   `PALWORLD_DIR/steamapps/appmanifest_2394010.acf`, como arquivo regular sem
   symlink, e a conectividade do host com o Steam.
4. Se o job falhou depois do Stop ou foi interrompido, trate o estado como
   ambíguo: não reenvie automaticamente o job. Verifique instalação, serviço,
   REST/health e logs do Palworld antes de decidir por nova tentativa ou
   recuperação manual com o backup pré-update preservado.

O Manager nunca executa rollback automático nem inicia silenciosamente o servidor
depois de uma falha do SteamCMD. A verificação manual pode ser repetida com
segurança quando não houver outro job incompatível ativo.

## Google Drive / rclone

1. Confirme o arquivo sem exibir conteúdo:

   ```bash
   stat -c '%U %G %a %n' /var/lib/palworld-manager/rclone/rclone.conf
   sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone listremotes
   sudo -u palmanager env RCLONE_CONFIG=/var/lib/palworld-manager/rclone/rclone.conf /usr/bin/rclone about palworld-manager: --json >/dev/null
   ```

2. O remote deve se chamar `palworld-manager`. Não execute `delete`,
   `purge`, `sync` ou retenção manual para diagnosticar; o namespace gerenciado
   continua limitado a `Palworld Manager/Backups/`.

## Discord

1. Confirme que o worker está ativo; a aplicação web apenas persiste o evento e
   nunca entrega a mensagem diretamente.
2. Consulte o estado do `notification_event`: falhas transitórias aguardam 5 e
   30 segundos, e a terceira tentativa termina em `FAILED`. Falhas permanentes,
   inclusive webhook ausente, terminam na primeira tentativa.
3. Em production, confirme que `DISCORD_WEBHOOK_URL` está definido no arquivo
   protegido de secrets e contém uma URL HTTPS oficial do Discord. Nunca copie a
   URL completa para logs, tickets ou capturas de tela.
4. Depois de corrigir a configuração, reinicie somente o worker. Eventos já
   terminais não são reenviados automaticamente; gere um novo evento controlado
   para validar a entrega.

Um evento deixado em `SENDING` por interrupção volta a `PENDING` quando ainda há
tentativas disponíveis. Como o Discord pode ter aceitado a requisição anterior,
essa recuperação at least once pode produzir uma mensagem duplicada.

## Tailscale

```bash
/usr/bin/tailscale status --json >/dev/null
/usr/bin/tailscale serve status --json
ss -ltn '( sport = :8080 )'
```

O Serve deve apontar para `127.0.0.1:8080`, a web deve escutar apenas nesse
loopback e Funnel deve permanecer desabilitado.

## Espaço em disco

Procedimento a validar para os estados warning e critical.

## SQLite / migrations

Confirme integridade e revisão sem editar o banco:

```bash
sudo -u palmanager sqlite3 -readonly /var/lib/palworld-manager/manager.db 'PRAGMA integrity_check;'
cd /opt/palworld-manager
/opt/palworld-manager/.venv/bin/alembic heads
```

Para aplicar migrations use somente o transient service documentado no
[runbook de produção](production-install.md), antes de iniciar as units. Não
edite tabelas ou `alembic_version` manualmente.

## Jobs interrompidos

O worker reconcilia jobs `RUNNING` como `INTERRUPTED` e nunca os recoloca na
fila. Verifique o estado real do Palworld, o backup preventivo e o log controlado
antes de decidir uma nova operação; Restore, Update e energia do host não são
retomados automaticamente.

## Deploy ou rollback falhou

Não repita o comando nem inicie serviços enquanto checkout, migration ou
ownership forem incertos. Preserve a saída sem `set -x`, confira o SHA atual e
o estado dos dois serviços separadamente. O commit anterior fica em
`/var/lib/palworld-manager/deploy/previous-commit`.

Rollback permanece manual e pode ser bloqueado quando o commit anterior não
reconhece a revisão Alembic atual. Não use `alembic downgrade`, não edite
`alembic_version` e não exponha EnvironmentFiles para diagnosticar. Siga
[Deploy recorrente e rollback](deploy.md).

Comece pela tela **Diagnóstico**, copie somente o relatório sanitizado e consulte
[Diagnóstico](diagnostics.md) para entender as fontes de cada check. Consulte
também [SPECIFICATION.md](../../SPECIFICATION.md).
