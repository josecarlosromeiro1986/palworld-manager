# Tailscale

> Status: Planejado para a V1.

Tailscale fornecerá a rede privada entre os dispositivos autorizados. Tailscale Serve publicará o FastAPI, que escutará somente em localhost, usando HTTPS dentro dessa rede privada.

O controle de dispositivos e identidade de rede permanecerá no Tailscale. O Manager terá sua própria autenticação de usuário, mas não implementará uma whitelist de dispositivos duplicada.

Tailscale Funnel, que exporia o serviço publicamente, não será usado na V1. A configuração operacional exata será documentada após a etapa de deploy ser implementada e validada.

Consulte [Segurança](../architecture/security.md) e [instalação em produção](../operations/production-install.md).
