# Troubleshooting

> Status: Planejado para a V1.

Este documento é um índice inicial e deverá crescer a partir de problemas reais e procedimentos validados. Enquanto os componentes não estiverem implementados, não há comandos de correção suportados para registrar.

Antes de qualquer intervenção futura, preservar logs e contexto, evitar alterações destrutivas e nunca incluir secrets no material de diagnóstico.

## Palworld Manager não inicia

Procedimento a validar durante a implementação de produção.

## Palworld não inicia

Procedimento a validar com a integração systemd e o health check.

## REST API indisponível

Procedimento a validar com o cliente oficial e seus erros tipados.

## SteamCMD

Procedimento a validar durante a implementação de updates.

## Google Drive / rclone

Procedimento a validar sem excluir arquivos fora da área gerenciada.

## Discord

Procedimento a validar sem exibir o webhook completo.

## Tailscale

Procedimento a validar para Serve, HTTPS e acesso privado.

## Espaço em disco

Procedimento a validar para os estados warning e critical.

## SQLite / migrations

Procedimento a validar com SQLAlchemy e Alembic.

## Jobs interrompidos

Procedimento a validar com a reconciliação de jobs; operações destrutivas não devem ser retomadas automaticamente.

Consulte também a futura tela de diagnóstico e [SPECIFICATION.md](../../SPECIFICATION.md).
