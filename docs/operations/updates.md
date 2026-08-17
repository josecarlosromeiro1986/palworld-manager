# Atualizações do Palworld

> Status: Implementado.

Atualizações são exclusivamente manuais. A ação **Verificar atualizações** cria
um job persistente, executado pelo worker, que não inicia o processo
automaticamente e não usa changelogs ou serviços de terceiros.

O Palworld Dedicated Server usa o App ID oficial `2394010`. A versão instalada é
o `buildid` do `steamapps/appmanifest_2394010.acf` pertencente ao diretório
estrutural `PALWORLD_DIR`. A versão disponível é o `buildid` da branch pública
obtida pelo próprio SteamCMD. A data só é exibida quando o `timeupdated` oficial
for um timestamp Unix válido. Dados ausentes ou respostas inválidas nunca são
preenchidos por inferência.

O fluxo implementado é:

```text
verificar espaço e versão novamente sob maintenance lock
→ backup pré-update
→ desligamento assistido
→ stop seguro
→ SteamCMD (`app_update 2394010 validate`)
→ start
→ REST API e health check
→ validar versão e erros críticos
→ auditoria e Discord
```

Antes do Update, a UI exige confirmação explícita. O cancelamento é aceito
enquanto o job aguarda, cria o backup preventivo ou executa a contagem regressiva.
O worker fecha atomicamente esse ponto antes do Stop; a UI então informa que o
job não pode mais ser cancelado. O SteamCMD recebe uma lista fixa de argumentos,
login anônimo, App ID fixo e `PALWORLD_DIR` validado; nunca é chamado com
`shell=True`, input livre ou privilégios de `root`.

Após o Start, o health check respeita o timeout configurado, inicialmente 120
segundos. O sucesso exige REST/health ONLINE, versão instalada compatível com a
branch pública observada e ausência de novos erros críticos. Saída bruta do
SteamCMD não entra em logs, auditoria ou banco; somente categorias e metadados não
sensíveis são persistidos.

Não há Update automático nem rollback automático. Se o SteamCMD ou a validação
posterior falhar depois do Stop, o job termina com revisão manual obrigatória e
não tenta iniciar silenciosamente uma instalação potencialmente ambígua. O backup
pré-update válido permanece local e preservado para recuperação manual. Sucesso e
falha são auditados e criam o evento persistente previsto para entrega pelo
worker na Etapa 23. Consulte [jobs e locks](../architecture/jobs-and-locks.md),
[backup e restore](backup-restore.md) e [SPECIFICATION.md](../../SPECIFICATION.md).
