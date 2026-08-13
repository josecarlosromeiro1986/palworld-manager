---
name: privileged-operations
description: Projetar ou revisar operações privilegiadas e de host do Palworld Manager. Usar em systemd, sudoers, reboot, shutdown, sinais, SteamCMD, filesystem protegido, ownership ou permissões.
---

# Operações privilegiadas

Consulte [segurança](../../../docs/architecture/security.md), [produção](../../../docs/operations/production-install.md) e [SPECIFICATION.md](../../../SPECIFICATION.md).

1. Aplique menor privilégio. Execute web e worker como `palmanager`, nunca como `root`.
2. Nunca conceda `sudo ALL`. Libere apenas comandos ou scripts controlados, com executáveis e argumentos explicitamente permitidos.
3. Prefira `subprocess` com lista de argumentos e `shell=False`. Nunca interpole input externo em comandos.
4. Valide e normalize paths contra allowlists; proteja contra traversal e symlinks perigosos.
5. Restrinja serviços, sinais, ownership e permissões aos alvos definidos na configuração estrutural.
6. Exija confirmação forte para operações destrutivas e registre ações críticas na auditoria.
7. Nunca envie SIGKILL automaticamente. Use SIGTERM primeiro e SIGKILL apenas como último recurso após segunda confirmação explícita.
8. Em development e test, use fakes; não execute systemd, SteamCMD, reboot, shutdown ou sinais destrutivos no host.
9. Não exponha secrets em argumentos, logs, erros ou diagnósticos.
10. Pare diante de alvo, estado ou efeito destrutivo ambíguo e solicite decisão humana.
