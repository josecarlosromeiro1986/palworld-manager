# Controle do servidor Palworld

> Status: Start, Stop, Restart, desligamento assistido e encerramento forçado manual implementados.

O Dashboard oferece **Iniciar**, **Parar** e **Reiniciar**. Parar permite escolher Agora, 1, 5 ou 10 minutos, com 5 minutos selecionados por padrão. O editor do `PalWorldSettings.ini` também oferece o mesmo job de Restart depois de salvar uma alteração. Toda ação exige sessão autenticada, CSRF válido e confirmação.

A web não executa comandos privilegiados. Ela persiste um job no SQLite, registra a solicitação na auditoria e retorna um fragmento HTMX que acompanha o estado do job. O processo `palworld-manager-worker.service` adquire e executa o job.

## Fluxos

Start e Restart:

```text
confirmar
→ job PENDING
→ worker cria pedido enumerado em /run
→ systemd.path aciona o oneshot root
→ aguardar health ONLINE
→ SUCCEEDED ou timeout/falha
```

Stop assistido:

```text
consultar jogadores via GET /players
→ avisar jogadores online via POST /announce
→ contagem persistente e cancelável
→ aviso final, quando houver jogadores
→ job PENDING
→ worker cria pedido enumerado em /run
→ systemd.path aciona o oneshot root
→ aguardar health OFFLINE
→ confirmar porta REST fechada
→ SUCCEEDED ou timeout/falha
```

Durante a contagem, **Cancelar** permanece disponível somente até o ponto seguro imediatamente anterior ao Stop. **Forçar agora** ignora a espera restante, mas continua usando o Stop normal; não significa envio de sinal. Falha ao consultar jogadores ou enviar um aviso obrigatório mantém o servidor ligado e marca o job como falho.

## Escalada manual após falha do Stop

SIGTERM só pode ser solicitado após falha do Stop. O administrador precisa digitar `FORCAR`. Se esse job também falhar, a UI libera o último recurso, SIGKILL, que exige nova confirmação digitada como `SIGKILL`. Nenhum timeout ou erro promove SIGTERM para SIGKILL automaticamente.

Cada tentativa é auditada. A execução de SIGTERM ou SIGKILL também cria um `notification_event` `FORCED_SHUTDOWN`; a entrega ao Discord permanece exclusivamente com o worker.

Os timeouts são lidos no momento da criação do job e persistidos com ele:

| Ação | Chave operacional | Default |
| --- | --- | --- |
| Start | `start_timeout_seconds` | 120 s |
| Restart | `restart_timeout_seconds` | 120 s |
| Stop | `stop_timeout_seconds` | 60 s |

Os valores são mantidos em `app_settings` e editados em **Configurações do Painel**. Start/Restart aceitam de 1 a 600 segundos e Stop, de 1 a 300 segundos. Valor persistido inválido falha claramente e não executa o comando.

## Concorrência e segurança

Uma chave de coordenação com índice único parcial impede dois jobs de ciclo de vida simultaneamente em `PENDING` ou `RUNNING`, inclusive sob requisições concorrentes. A aquisição usa uma única atualização condicional e adquire o maintenance lock global na mesma transação. O worker mantém heartbeat a cada 10 segundos; se for interrompido, jobs em `RUNNING` passam para `INTERRUPTED`, liberam o lock e nunca são retomados automaticamente. O Dashboard mostra etapa, progresso e trecho do log textual de cada execução.

Em production, o adapter pode criar somente estes pedidos vazios e exclusivos:

```text
/run/palworld-manager/host-control/palworld-start.request
/run/palworld-manager/host-control/palworld-stop.request
/run/palworld-manager/host-control/palworld-restart.request
/run/palworld-manager/host-control/palworld-sigterm.request
/run/palworld-manager/host-control/palworld-sigkill.request
```

Cada instância `systemd.path` aceita somente um desses nomes e aciona uma unit
`oneshot` que executa como root um único branch fixo do helper para
`palworld.service`. Em production, `PALWORLD_SERVICE` deve manter esse valor;
uma unidade alternativa exige alterar simultaneamente adapter, helper, templates
systemd e configuração estrutural. Não há grant Polkit, sudo, serviço ou
argumento genérico.

Development e test usam um fake compartilhado via SQLite entre web e worker. Assim, um job concluído atualiza também o health exibido pelo Dashboard, sem executar systemd ou abrir conexão com o Palworld real.
