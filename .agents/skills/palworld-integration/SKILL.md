---
name: palworld-integration
description: Trabalhar nas integrações oficiais do Palworld Manager. Usar em REST API do Palworld, jogadores, anúncios, Kick, Ban, Unban, health check, SteamCMD ou PalWorldSettings.ini.
---

# Integração com Palworld

Consulte a [integração REST](../../../docs/integrations/palworld-rest-api.md), a [visão geral](../../../docs/architecture/overview.md) e os requisitos específicos em [SPECIFICATION.md](../../../SPECIFICATION.md).

1. Confirme endpoints e comportamento na documentação oficial vigente antes de implementar uma integração real.
2. Não invente endpoints, campos, limites ou semântica de erro.
3. Isole REST API, SteamCMD e leitura/escrita do INI atrás de adapters ou services testáveis.
4. Forneça fakes controláveis para development e test; não dependa de servidor Palworld real.
5. Consulte jogadores somente sob demanda ou quando uma operação precisar; não implemente polling contínuo na V1.
6. Não leia nem modifique saves para descobrir jogadores offline.
7. Use somente mecanismos oficiais para anúncios, Kick, Ban, Unban e salvamento seguro.
8. Preserve parâmetros desconhecidos do `PalWorldSettings.ini`; valide conhecidos e nunca descarte conteúdo não reconhecido.
9. Trate SteamCMD e controle do servidor como operações sensíveis; use também `$privileged-operations` e `$jobs-worker` quando aplicável.
10. Cubra sucesso, timeout, autenticação, indisponibilidade, resposta inválida e falhas inesperadas com testes contra fakes.
