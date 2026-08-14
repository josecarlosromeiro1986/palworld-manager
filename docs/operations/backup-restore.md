# Backup e restore

> Status: Backup local implementado. Restore local e integração com Google Drive permanecem planejados para as etapas próprias.

Este documento resume a operação prevista. [SPECIFICATION.md](../../SPECIFICATION.md) contém os requisitos oficiais completos.

## Backup

O backup automático é diário às `04:00` no timezone configurado, inicialmente `America/Sao_Paulo`. O worker cria no máximo um job automático por data local e respeita `backup_enabled`, `backup_time` e `timezone` quando esses valores operacionais já existem no SQLite; a interface completa para editá-los permanece na Etapa 24. O Manager mantém exatamente 3 backups locais válidos. A retenção de até 10 backups próprios no Google Drive permanece planejada.

Cada backup será um `.tar.gz` com `manifest.json`, hash SHA-256 e teste de integridade. Só será considerado válido após essas verificações. Antes da cópia, o Manager solicitará um salvamento seguro pelo mecanismo oficial disponível; uma falha nessa etapa invalida a operação.

A integridade possui duas camadas e não usa sidecar `.sha256`:

- `backup_records.sha256` armazena o SHA-256 dos bytes do `.tar.gz` final;
- `manifest.json` informa versão do schema, identificador do backup, timestamp UTC, metadados não sensíveis e a lista determinística do payload;
- cada arquivo do payload possui path relativo determinístico, tamanho e SHA-256 individual;
- o próprio `manifest.json` não aparece na lista, e o hash externo do arquivo não aparece no manifest.

Um Restore futuro deverá validar primeiro o hash externo contra o registro persistido e depois conferir cada item do payload contra o manifest. Paths absolutos e dados sensíveis são proibidos no manifest.

O conteúdo previsto inclui:

- mundo completo, inclusive `Players/` e demais dados persistentes;
- configurações relevantes do Palworld;
- cópia consistente de `/var/lib/palworld-manager/manager.db`;
- configurações não sensíveis do Manager;
- manifest com os metadados necessários à validação.

O backup excluirá:

- secrets, tokens, credenciais e webhooks;
- binários do servidor e SteamCMD;
- a própria área de backups e qualquer cópia recursiva;
- conteúdo não pertencente ao conjunto explicitamente gerenciado.

### Fluxo local implementado

FastAPI apenas cria e acompanha o job `LOCAL_BACKUP`. O worker o adquire atomicamente, mantém o maintenance lock global e executa:

```text
POST /save oficial
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

O mundo vem exclusivamente de `PALWORLD_DIR/Pal/Saved/SaveGames`; `Players/` é copiado e hasheado como conteúdo opaco, sem interpretação. Diretórios internos chamados `backup` ou `backups` e qualquer `secrets.env` são excluídos. `PalWorldSettings.ini` é incluído com campos sensíveis reconhecidos ou suspeitos vazios, e `GameUserSettings.ini`, quando presente, recebe a mesma proteção por nome de chave. As configurações do Manager usam uma allowlist de valores operacionais não sensíveis.

O staging fica sob a área de dados do Manager. O arquivo final usa referência relativa `backups/<nome-gerenciado>.tar.gz`, permissão `0640` e só é publicado após validação integral. Links simbólicos, entradas não regulares, paths absolutos, traversal, escape da raiz e configurações acima do limite são recusados. Falha antes ou depois da publicação remove somente o artefato reconhecido da tentativa atual e nunca cria um `backup_record` válido.

O cancelamento é aceito até o último checkpoint anterior à publicação. Ao entrar na publicação atômica, o job fecha `is_cancellable` e a interface informa que não pode mais cancelar. Após interrupção do worker, o job vira `INTERRUPTED`, não é retomado e o startup remove somente temporários e artefatos finais cujo nome contém o ID desse job interrompido.

A retenção consulta registros `LOCAL` e `VALID`, reconhece também o namespace e o padrão de nome gerenciado e remove somente os excedentes mais antigos. Arquivos sem registro ou fora desse padrão são preservados.

## Google Drive

rclone fará uploads e downloads em uma pasta ou namespace exclusivo do Palworld Manager. Antes do upload, o sistema verificará quota e aplicará retenção somente aos próprios backups. Se o espaço gratuito continuar insuficiente, o upload será cancelado, o backup local será preservado e a falha será auditada.

**O Manager nunca excluirá arquivos externos à área de backups que administra.** Nenhum plano pago será requisito.

## Restore

O restore local ou remoto seguirá o fluxo:

```text
maintenance lock
→ validação do backup
→ download temporário, se remoto
→ SHA-256 e integridade
→ backup preventivo
→ stop seguro
→ substituição dos arquivos
→ ownership e permissões
→ start
→ REST API e health check
→ conclusão ou falha auditada
```

A operação exigirá a confirmação exata `RESTAURAR`. O arquivo será validado contra traversal, symlinks perigosos, formato inválido e espaço insuficiente antes de alterar o mundo. Temporários só serão removidos quando for seguro.

Não haverá rollback automático. Em caso de falha, o backup preventivo será preservado e a recuperação dependerá de decisão humana. A V1 não restaurará um jogador isoladamente.
