# Discord

> Status: Implementado na Etapa 23.

A integração usa webhook, sem bot permanente. A política é enxuta: sucessos rotineiros permanecem na auditoria, enquanto o Discord recebe principalmente falhas e eventos administrativos importantes.

Os eventos suportados incluem crash sem recuperação, falha de backup ou Drive, disco crítico, Update ou Restore concluído/falho, bloqueio de login, job crítico interrompido e encerramento forçado.

O webhook é o secret estrutural `DISCORD_WEBHOOK_URL`, mantido fora do SQLite.
Em production, quando configurado, ele deve usar HTTPS e o endpoint oficial do
Discord, sem query, fragmento ou credenciais adicionais. A ausência não impede
o startup, mas uma entrega pendente termina como falha permanente e controlada;
nenhum evento é descartado silenciosamente. A interface de teste implementada
em **Configurações do Painel** nunca mostra o valor do webhook. Logs, auditoria,
diagnósticos e mensagens de erro também não reproduzem a URL ou o token. A web cria somente
um `notification_event` `DISCORD_TEST` de conteúdo fixo e acompanha seu estado;
a web não acessa o webhook. Solicitações repetidas enquanto o teste está
`PENDING` ou `SENDING` reutilizam o evento ativo.

## Configuração de produção

### 1. Criar o webhook no Discord

A conta que executa esta etapa precisa conseguir gerenciar webhooks no servidor
Discord e no canal escolhido:

1. abra **Configurações do servidor > Integrações > Webhooks**;
2. selecione **Novo webhook** ou **Criar webhook**;
3. defina um nome identificável, por exemplo `Palworld Manager`;
4. selecione o canal que receberá os alertas;
5. copie a URL do webhook e guarde-a como secret.

A URL concede capacidade de publicar no canal. Não a cole no chat, em issue,
log, screenshot, comando gravado no histórico ou arquivo versionado. O
procedimento oficial do Discord está em
[Intro to Webhooks](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks).

### 2. Instalar o secret

Crie ou edite o arquivo protegido sem colocar a URL na linha de comando:

```bash
sudo test -e /etc/palworld-manager/secrets.env || sudo install -o root -g palmanager -m 0640 /dev/null /etc/palworld-manager/secrets.env
sudoedit /etc/palworld-manager/secrets.env
```

Preserve as credenciais REST já existentes e adicione exatamente uma linha:

```text
DISCORD_WEBHOOK_URL='URL_DO_WEBHOOK'
```

Valide somente owner, grupo, modo, presença da variável e leitura pelo worker.
Os comandos abaixo não exibem o valor:

```bash
sudo stat -c '%U %G %a %n' /etc/palworld-manager/secrets.env
sudo grep -Eq '^DISCORD_WEBHOOK_URL=' /etc/palworld-manager/secrets.env
sudo -u palmanager test -r /etc/palworld-manager/secrets.env
```

O resultado de `stat` deve ser `root palmanager 640`. Reinicie somente o
worker para carregar o secret:

```bash
sudo systemctl restart palworld-manager-worker.service
systemctl is-active --quiet palworld-manager-worker.service
```

### 3. Validar a entrega

Entre como `ADMIN`, abra **Configurações do Painel** e solicite o teste do
Discord. O evento deve chegar ao canal e terminar como `SENT`. Se permanecer
pendente, confirme primeiro o heartbeat do worker; se falhar, consulte o job e o
journal por categoria, sem imprimir o ambiente do processo nem o arquivo de
secrets.

Para rotacionar a integração, crie outro webhook, substitua a linha por
`sudoedit`, reinicie o worker e conclua um novo teste antes de excluir o
webhook antigo. Se a URL for exposta, exclua imediatamente o webhook no Discord.

## Entrega

FastAPI ou worker podem criar um `notification_event` no SQLite. Somente o worker pode consumir o evento e entregar a mensagem ao Discord; FastAPI não acessará o webhook para envio direto.

```text
FastAPI ou Worker
→ notification_events no SQLite
→ Worker
→ Discord
```

Os estados são `PENDING`, `SENDING`, `SENT` e `FAILED`. O claim e o incremento
da tentativa ocorrem atomicamente antes do acesso externo. Falhas transitórias
aguardam 5 segundos depois da primeira tentativa e 30 segundos depois da
segunda. A terceira falha é terminal; uma falha permanente termina já na
primeira tentativa. Não existe loop infinito.

A entrega é at least once. Se o worker reiniciar com um evento deixado em `SENDING`, ele retorna a `PENDING` quando houver menos de 3 tentativas e a próxima entrega conta como nova tentativa; com 3 tentativas, passa a `FAILED`. Como o Discord pode ter aceitado a mensagem antes da interrupção, essa recuperação admite eventual duplicidade.

O adapter envia `POST` JSON com timeout curto. O conteúdo é montado somente a
partir de textos versionados por tipo, IDs numéricos e timestamp UTC; resultados
livres de jobs, nomes de usuário, paths e erros externos não são copiados. O
payload define `allowed_mentions` vazio, limita o tamanho e ignora o corpo da
resposta. Development e test selecionam um fake integral em memória e não abrem
rede.

Os eventos oficiais estão definidos em [SPECIFICATION.md](../../SPECIFICATION.md).
