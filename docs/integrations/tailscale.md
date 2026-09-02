# Tailscale

> Status: Implementado operacionalmente na Etapa 29.

Tailscale fornece a rede privada entre os dispositivos autorizados. A aplicação
web escuta somente em `127.0.0.1:8080`; Tailscale Serve termina HTTPS e
encaminha o tráfego privado para esse listener:

```bash
sudo /usr/bin/tailscale serve --bg 127.0.0.1:8080
/usr/bin/tailscale status --json >/dev/null
/usr/bin/tailscale serve status --json
```

O modo `--bg` mantém a configuração após reinício do daemon. O destino deve
permanecer exatamente em loopback e a saída de status deve corresponder a
`127.0.0.1:8080`.

Tailscale Funnel não faz parte da V1 e não deve ser habilitado. O controle de
dispositivos e identidade de rede permanece no Tailscale; o Manager mantém sua
própria autenticação e não duplica uma whitelist de dispositivos.

Depois da configuração, um dispositivo autorizado deve abrir a URL HTTPS
exibida pelo Serve e validar login/logout. A tela **Diagnóstico** também consulta
`tailscale status --json` e `tailscale serve status --json`, sem alterar a
configuração.

Consulte [Segurança](../architecture/security.md), o
[runbook de produção](../operations/production-install.md) e a
[documentação oficial do Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).
