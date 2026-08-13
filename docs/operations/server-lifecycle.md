# Controle do servidor Palworld

> Status: Start, Stop e Restart implementados. Desligamento assistido e encerramento forçado permanecem planejados.

O Dashboard oferece as ações **Iniciar**, **Parar** e **Reiniciar**. Cada botão apresenta uma confirmação antes do envio, e o backend exige sessão autenticada, CSRF válido e o valor exato da ação.

A web não executa comandos privilegiados. Ela persiste um job no SQLite, registra a solicitação na auditoria e retorna um fragmento HTMX que acompanha o estado do job. O processo `palworld-manager-worker.service` adquire e executa o job.

## Fluxos

Start e Restart:

```text
confirmar
→ job PENDING
→ worker executa systemctl --no-block
→ aguardar health ONLINE
→ SUCCEEDED ou timeout/falha
```

Stop:

```text
confirmar
→ job PENDING
→ worker executa systemctl --no-block
→ aguardar health OFFLINE
→ confirmar porta REST fechada
→ SUCCEEDED ou timeout/falha
```

Os timeouts são lidos no momento da criação do job e persistidos com ele:

| Ação | Chave operacional | Default |
| --- | --- | --- |
| Start | `start_timeout_seconds` | 120 s |
| Restart | `restart_timeout_seconds` | 120 s |
| Stop | `stop_timeout_seconds` | 60 s |

Os valores podem ser mantidos em `app_settings`; sua edição pelo painel será entregue na etapa de Configurações do Painel. Start/Restart aceitam de 1 a 600 segundos e Stop, de 1 a 300 segundos. Valor persistido inválido falha claramente e não executa o comando.

## Concorrência e segurança

Uma chave de coordenação com índice único parcial impede dois jobs de ciclo de vida simultaneamente em `PENDING` ou `RUNNING`, inclusive sob requisições concorrentes. A aquisição do próximo job usa uma única atualização condicional. A infraestrutura completa de maintenance lock, heartbeat e recuperação do worker continua reservada à Etapa 17.

Em production, somente estes comandos são construídos pelo adapter, sempre com argumentos separados e sem shell:

```text
/usr/bin/sudo --non-interactive /usr/bin/systemctl --no-block start palworld.service
/usr/bin/sudo --non-interactive /usr/bin/systemctl --no-block stop palworld.service
/usr/bin/sudo --non-interactive /usr/bin/systemctl --no-block restart palworld.service
```

O nome real da unidade vem de `PALWORLD_SERVICE` e passa pela allowlist estrutural. A regra mínima de sudoers será instalada e validada na etapa de deploy; não existe `sudo ALL`.

Development e test usam um fake compartilhado via SQLite entre web e worker. Assim, um job concluído atualiza também o health exibido pelo Dashboard, sem executar systemd ou abrir conexão com o Palworld real.
