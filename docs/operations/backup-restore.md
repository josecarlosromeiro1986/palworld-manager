# Backup e restore

> Status: Backup e Restore locais e remotos implementados.

Este documento resume a operação prevista. [SPECIFICATION.md](../../SPECIFICATION.md) contém os requisitos oficiais completos.

## Backup

O backup automático é diário às `04:00` no timezone configurado, inicialmente `America/Sao_Paulo`. O worker cria no máximo um job automático por data local e respeita `backup_enabled`, `backup_time` e `timezone` editáveis em **Configurações do Painel**. A retenção inicial é de 3 backups locais válidos e até 10 backups remotos gerenciados. O painel aceita de 1 a 30 backups locais e de 1 a 100 backups no Drive, com validação obrigatória no backend; criação, download, Restore e Update aplicam a retenção configurada sem remover artefatos externos.

Cada backup é um `.tar.gz` com `manifest.json`, hash SHA-256 e teste de integridade. Só é considerado válido após essas verificações. Antes da cópia, o Manager consulta o health do Palworld. Se estiver `ONLINE`, solicita um salvamento seguro pelo mecanismo oficial disponível e uma falha nessa etapa invalida a operação. Se estiver comprovadamente `OFFLINE`, copia diretamente os arquivos já fechados sem chamar a REST API. Estados `INICIANDO`, `DEGRADADO` ou `FALHA` são ambíguos e encerram a operação sem artefato válido. Essa regra é compartilhada por backups manuais, automáticos e preventivos.

A integridade possui duas camadas e não usa sidecar `.sha256`:

- `backup_records.sha256` armazena o SHA-256 dos bytes do `.tar.gz` final;
- `manifest.json` informa versão do schema, identificador do backup, timestamp UTC, metadados não sensíveis e a lista determinística do payload;
- cada arquivo do payload possui path relativo determinístico, tamanho e SHA-256 individual;
- o próprio `manifest.json` não aparece na lista, e o hash externo do arquivo não aparece no manifest.

O Restore local valida primeiro o hash externo contra o registro persistido e depois confere cada item do payload contra o manifest. Paths absolutos e dados sensíveis são proibidos no manifest.

O conteúdo previsto inclui:

- mundo completo, inclusive `Players/` e demais dados persistentes;
- configurações relevantes do Palworld;
- cópia consistente de `/var/lib/palworld-manager/manager.db`;
- configurações não sensíveis do Manager;
- manifest com os metadados necessários à validação.

O subtree `manager/` é incluído para recuperação manual e offline de desastre.
Embora seus arquivos participem integralmente do manifest e da validação do
artefato, eles não são aplicados pelo Restore normal do painel.

O backup excluirá:

- secrets, tokens, credenciais e webhooks;
- binários do servidor e SteamCMD;
- a própria área de backups e qualquer cópia recursiva;
- conteúdo não pertencente ao conjunto explicitamente gerenciado.

### Fluxo local implementado

FastAPI apenas cria e acompanha o job `LOCAL_BACKUP`. O worker o adquire atomicamente, mantém o maintenance lock global e executa:

```text
health do Palworld
→ POST /save oficial quando ONLINE, ou snapshot direto quando OFFLINE
→ staging controlado
→ cópia opaca do mundo completo
→ configurações permitidas sem valores sensíveis
→ snapshot consistente do SQLite com integrity_check
→ manifest determinístico
→ criação e validação do tar.gz
→ SHA-256 externo
→ publicação atômica
→ backup_record válido, auditoria e retenção
```

Enquanto o job está ativo, a página **Backups** atualiza etapa, progresso e log a cada segundo. Ao chegar a um estado terminal, o fragmento do job dispara uma atualização única da lista de backups locais, de modo que um novo artefato válido apareça sem recarregar a página inteira.

O mundo vem exclusivamente de `PALWORLD_DIR/Pal/Saved/SaveGames`; `Players/` é copiado e hasheado como conteúdo opaco, sem interpretação. Diretórios internos chamados `backup` ou `backups` e qualquer `secrets.env` são excluídos. `PalWorldSettings.ini` é incluído com campos sensíveis reconhecidos ou suspeitos vazios, e `GameUserSettings.ini`, quando presente, recebe a mesma proteção por nome de chave. As configurações do Manager usam uma allowlist de valores operacionais não sensíveis.

O staging fica sob a área de dados do Manager. O arquivo final usa referência relativa `backups/<nome-gerenciado>.tar.gz`, permissão `0640` e só é publicado após validação integral. Links simbólicos, entradas não regulares, paths absolutos, traversal, escape da raiz e configurações acima do limite são recusados. Falha antes ou depois da publicação remove somente o artefato reconhecido da tentativa atual e nunca cria um `backup_record` válido.

O cancelamento é aceito até o último checkpoint anterior à publicação. Ao entrar na publicação atômica, o job fecha `is_cancellable` e a interface informa que não pode mais cancelar. Após interrupção do worker, o job vira `INTERRUPTED` e não é retomado. O startup remove somente temporários e artefatos finais pertencentes a jobs `LOCAL_BACKUP` interrompidos; um backup preventivo já registrado por um Restore nunca entra nessa limpeza.

A retenção consulta registros `LOCAL` e `VALID`, reconhece também o namespace e o padrão de nome gerenciado e remove somente os excedentes mais antigos. Arquivos sem registro ou fora desse padrão são preservados.

## Google Drive

rclone faz uploads e downloads no namespace exclusivo do Palworld Manager. Antes do upload, o sistema verifica quota e aplica retenção somente aos próprios backups. Se o espaço gratuito continuar insuficiente, o upload é cancelado, o backup local é preservado e a falha é auditada.

Um backup diário automático gera um job separado de upload somente depois que o
artefato local estiver concluído, integralmente validado, com SHA-256 calculado
e registrado como `LOCAL` e `VALID`. Backups manuais e preventivos permanecem
somente locais por padrão, mas qualquer backup local válido pode ser enviado
manualmente pelo painel. Falha remota não altera a validade nem remove o arquivo
local.

Upload, download e exclusão são jobs persistentes do worker. O download desta
etapa apenas importa uma cópia remota validada para a área local; ele não inicia
Restore nem toca no mundo. O arquivo baixado só é publicado após conferir
SHA-256 externo, tar.gz, manifest e payload. A conexão e a quota também são
testadas pelo worker, e a interface mostra apenas resultados persistidos, sem
paths estruturais.

A retenção remota considera somente linhas `DRIVE` e `VALID` cujo nome e path
correspondem ao formato gerenciado. Para liberar quota real no Google Drive, a
remoção desses alvos exatos é permanente; nenhum diretório, arquivo sem registro
ou item fora de `Palworld Manager/Backups/` é removido. Uma falha cria auditoria
segura e `notification_event` `DRIVE_FAILED`, cuja entrega externa permanece na
Etapa 23.

**O Manager nunca excluirá arquivos externos à área de backups que administra.** Nenhum plano pago será requisito.

## Restore local implementado

FastAPI exige sessão, CSRF e a confirmação literal `RESTAURAR`, mas apenas cria e acompanha o job persistente `LOCAL_RESTORE`. O worker adquire atomicamente o job e o maintenance lock global. O fluxo implementado é:

```text
maintenance lock
→ cópia controlada do backup local para staging
→ SHA-256 externo contra backup_records
→ tar.gz, manifest e hashes individuais
→ validação semântica de world/, config/ e manager/
→ merge e validação dos INIs atuais
→ verificação de espaço
→ backup preventivo
→ stop seguro
→ substituição de world/ e configurações do Palworld
→ ownership de grupo e permissões mínimas
→ start
→ REST API e health check
→ verificação de logs críticos
→ conclusão ou falha auditada
```

A cópia temporária tem o SHA-256 conferido antes de o arquivo ser aberto para validação interna. A inspeção exige `manifest.json` como primeira entrada, limita o manifest a 4 MiB, cada path a 4096 bytes, o arquivo a 100.000 entradas e o payload declarado a 128 GiB. A validação é interrompida durante a leitura dos headers, antes de avançar sobre um payload declarado excessivo. A extração não usa `extractall`: aceita somente arquivos regulares com paths relativos presentes no manifest e recusa traversal, paths absolutos, links, duplicidade, conteúdo extra e escape da área temporária. O mundo precisa conter `Level.sav`, `LevelMeta.sav` e `Players/`; saves de jogadores permanecem opacos. `manager/manager.db` é aberto como snapshot SQLite somente leitura e imutável para executar `PRAGMA integrity_check` sem criar sidecars no payload; `manager/settings.json` passa pela allowlist e tipos esperados. Nenhum dos dois é aplicado.

Todas as validações e preparações possíveis terminam antes do Stop. Isso inclui a leitura do `PalWorldSettings.ini` atual, o merge conservador, a validação do resultado e o espaço livre no staging e no destino para o tamanho não comprimido declarado. O tamanho efetivamente extraído precisa coincidir com o manifest e o destino é verificado novamente depois do staging. Se `GameUserSettings.ini` estiver no backup, seus campos com nomes sensíveis também vêm obrigatoriamente do arquivo atual. Uma falha nessa fase não solicita Stop nem altera o mundo.

Depois da pré-validação, o backup preventivo reutiliza a mesma regra de
consistência do pipeline local: executa novo `POST /save` oficial quando o
Palworld está `ONLINE` ou copia diretamente quando o health confirma
`OFFLINE`. Ele recebe `backup_record` válido antes do Stop e é preservado
tanto no sucesso quanto na falha; a retenção continua exatamente em 3 arquivos
gerenciados, protegendo durante a operação a origem e o preventivo. Arquivos
externos permanecem intocados.

O Stop só é aceito quando o serviço está offline e a porta REST está fechada. A publicação usa nomes temporários controlados no mesmo filesystem, mantém o grupo do mundo anterior e aplica `0770` em diretórios e `0660` em arquivos. O worker continua não-root e precisa apenas pertencer ao grupo compartilhado com o Palworld; nenhum comando usa shell. O Start só conclui após health `ONLINE`, incluindo `/info` da REST API oficial, e ausência de erros críticos posteriores ao início.

A verificação pós-Start usa um detector operacional próprio, separado da
categoria visual `ERROR` da página de logs. Cabeçalhos informativos que contêm
`x-sentry-error` e o encerramento esperado por SIGTERM com status 143 durante o
fluxo não invalidam um Restore que voltou a `ONLINE`; prioridades críticas e
assinaturas explícitas de falha continuam bloqueando a conclusão.

O job não é cancelável em nenhuma fase. Não há rollback automático. Falha ou interrupção depois do início da substituição marca `requires_manual_review`; o worker não retoma o job e não remove automaticamente diretórios estruturais de estado ambíguo. O staging isolado do Manager é removido ao fim de uma execução conhecida; após encerramento abrupto do processo, permanece para inspeção segura. A V1 não restaura um jogador isoladamente.

O Restore do painel aplica somente `world/` e as configurações do Palworld. Ele
nunca substitui `manager/manager.db` nem aplica `manager/settings.json`:
usuários, sessões, auditoria, jobs e configurações atuais do Manager permanecem
intactos. Web e worker não são parados para trocar o banco ativo, e esta etapa
não cria executor externo ou fluxo de Restore completo do Manager.

Como `PalWorldSettings.ini` entra no backup com campos sensíveis sanitizados, a
pré-validação combina os campos não sensíveis do backup com os valores
sensíveis do arquivo atual. O merge preserva também parâmetros desconhecidos e
é integralmente validado antes do Stop. O Manager nunca inventa secrets nem
aplica valores vazios sanitizados sobre os atuais. Arquivo atual ausente,
ilegível, inválido ou sem combinação determinística encerra o job antes de
alterar o servidor; a auditoria recebe apenas a categoria segura da falha.

A página **Backups** mostra um formulário por artefato válido, progresso e log controlado do Restore a cada segundo. Ao terminar, atualiza a lista sem refresh manual, exibe o backup preventivo e informa quando revisão humana é necessária. Paths de armazenamento e detalhes internos não são expostos.

Development e test usam `FakeRestoreTarget`, REST, ciclo de vida e logs simulados. Nesses ambientes o worker não lê nem escreve mundo, INIs, systemd ou filesystem estrutural reais do Palworld.

## Restore remoto implementado

Cada registro remoto `DRIVE` e `VALID` oferece no painel um Restore com a mesma
confirmação literal `RESTAURAR`. FastAPI cria o job `REMOTE_RESTORE`; somente o
worker executa o download e o restante da operação. `LOCAL_RESTORE` e
`REMOTE_RESTORE` compartilham a mesma chave de coordenação e o maintenance lock,
impedindo sobreposição entre si e com outras operações incompatíveis.

O worker baixa para uma área exclusiva em `tmp/drive/`, confere tamanho e
SHA-256 contra `backup_records` e valida o tar.gz antes de repassar o artefato ao
pipeline local. Esse pipeline repete a validação externa, confere manifest,
payload e disaster recovery, combina os INIs atuais e verifica espaço antes de
criar o backup preventivo e solicitar o Stop. Nenhuma cópia `LOCAL` é criada
pelo download temporário, portanto ele não altera a retenção local.

O job não é cancelável, assim como o Restore local, e não é retomado depois de
uma interrupção. Falha antes do Stop remove somente o staging conhecido, não
toca no mundo e não remove nem invalida o objeto remoto. Falha depois da
substituição mantém o backup preventivo, preserva o backup remoto, não executa
rollback automático e exige revisão manual. O escopo restaurado continua sendo
somente mundo e configurações do Palworld; `manager/` permanece reservado à
recuperação manual/offline.
