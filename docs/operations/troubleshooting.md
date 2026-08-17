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

1. Consulte o job na página **Atualizações** e confirme a categoria pública da
   falha; a saída bruta do SteamCMD não é exposta pela UI nem persistida.
2. Verifique se o worker não-root consegue executar o path estrutural `STEAMCMD`
   e ler/escrever somente a instalação em `PALWORLD_DIR`.
3. Confirme a presença de
   `PALWORLD_DIR/steamapps/appmanifest_2394010.acf`, como arquivo regular sem
   symlink, e a conectividade do host com o Steam.
4. Se o job falhou depois do Stop ou foi interrompido, trate o estado como
   ambíguo: não reenvie automaticamente o job. Verifique instalação, serviço,
   REST/health e logs do Palworld antes de decidir por nova tentativa ou
   recuperação manual com o backup pré-update preservado.

O Manager nunca executa rollback automático nem inicia silenciosamente o servidor
depois de uma falha do SteamCMD. A verificação manual pode ser repetida com
segurança quando não houver outro job incompatível ativo.

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
