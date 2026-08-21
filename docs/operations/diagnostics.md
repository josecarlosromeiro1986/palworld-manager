# Diagnóstico

> Status: Implementado na Etapa 25.

A página autenticada **Diagnóstico** reúne checks somente leitura do Manager,
do Palworld e das integrações. Ela não cria jobs, não adquire o maintenance
lock, não altera configurações e não executa comandos de correção.

Cada check usa um dos estados previstos:

- `✓ OK`: o sinal consultado está saudável;
- `⚠ Atenção`: existe estado operacional aceitável que merece revisão ou
  ainda não há resultado conhecido;
- `✗ Falha`: a validação encontrou indisponibilidade, inconsistência ou
  resultado inválido.

## Checks implementados

O relatório combina:

- versão do Manager e commit Git conhecido;
- serviço web e processo principal no systemd;
- serviço, processo e REST API do Palworld pelo health check compartilhado;
- serviço do worker e heartbeat persistido no SQLite;
- portas locais do Manager e da REST API;
- uso de RAM, espaço livre e limites operacionais de disco;
- tipo, ausência de symlink e acesso necessário aos diretórios, banco,
  configuração e executáveis estruturais;
- último check de versão do SteamCMD executado pelo worker;
- últimos testes seguros de rclone/Google Drive e Discord executados pelo
  worker;
- estado do Tailscale e destino configurado no Serve;
- `PRAGMA integrity_check` do SQLite e coincidência entre a migration aplicada
  e o head do Alembic;
- contagem de erros e avisos nas últimas 100 linhas redigidas do Palworld e de
  jobs falhos ou interrompidos nas últimas 24 horas.

SteamCMD, rclone e Discord continuam respeitando a separação entre web e
worker. A página apenas lê seus últimos resultados persistidos; ela não chama
esses executáveis ou serviços externos. Em production, as consultas diretas de
host usam argumentos fixos e sem shell. Tailscale é consultado somente por
`tailscale status --json` e `tailscale serve status --json`.

Development e test usam fakes integrais para os sinais de host. Eles não
consultam systemd, Tailscale, portas, paths estruturais, SteamCMD, rclone,
Google Drive ou Discord reais.

## Relatório copiável

**Testar novamente** refaz somente as leituras e substitui o relatório por
HTMX. **Copiar diagnóstico** envia para a área de transferência apenas os
estados e resumos controlados exibidos no relatório.

O conteúdo copiável nunca inclui:

- senhas, tokens, webhooks, cookies ou credenciais;
- URLs autenticadas, headers ou respostas externas brutas;
- stderr, mensagens livres de exceção ou saídas brutas de comandos;
- paths estruturais ou identificadores do worker.

Consulte [Troubleshooting](troubleshooting.md) para a investigação posterior
de um check com Atenção ou Falha e
[SPECIFICATION.md](../../SPECIFICATION.md) para os requisitos oficiais.
