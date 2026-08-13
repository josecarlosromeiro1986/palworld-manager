# Atualizações do Palworld

> Status: Planejado para a V1.

Atualizações serão exclusivamente manuais. A ação **Verificar atualizações** consultará versões sem iniciar o processo automaticamente e sem usar changelogs de terceiros.

O fluxo planejado é:

```text
verificar espaço e versão
→ maintenance lock
→ backup pré-update
→ desligamento assistido
→ stop seguro
→ SteamCMD
→ start
→ REST API e health check
→ validar versão e erros críticos
→ auditoria e Discord
```

Após o start, o health check respeitará o timeout configurado, inicialmente 120 segundos. O SteamCMD não poderá ser cancelado enquanto estiver modificando arquivos. Sucesso ou falha serão auditados; o Discord receberá os eventos administrativos previstos na política de notificações.

Não haverá update automático nem rollback automático. O backup pré-update será preservado para uma eventual recuperação manual. Consulte [jobs e locks](../architecture/jobs-and-locks.md), [backup e restore](backup-restore.md) e [SPECIFICATION.md](../../SPECIFICATION.md).
