# Política de Privacidade do Palworld Manager

**Última atualização:** 27 de agosto de 2026.

Esta política descreve o tratamento de dados do Google pela integração opcional entre o Palworld Manager e o Google Drive. O Palworld Manager é uma aplicação auto-hospedada: cada administrador controla a própria instalação, a conta Google autorizada e os backups produzidos.

## Dados acessados

A integração usa OAuth e rclone para:

- consultar a quota disponível da conta Google Drive autorizada;
- criar e listar arquivos de backup no namespace fixo `Palworld Manager/Backups/`;
- enviar, conferir, baixar e restaurar backups gerenciados;
- excluir apenas backups reconhecidos pelo Manager quando solicitado pelo administrador ou pela retenção configurada.

A autorização OAuth pode conceder ao rclone um escopo amplo do Google Drive. Apesar disso, o Palworld Manager limita suas operações ao namespace acima e não adota nem remove arquivos externos a ele.

## Uso e armazenamento

Os dados acessados são usados somente para criar, validar, transferir, listar, restaurar e aplicar retenção aos backups solicitados pelo administrador. O aplicativo não usa dados do Google para publicidade, perfilamento ou venda.

O token OAuth permanece no arquivo local de configuração do rclone, fora do repositório, do banco de dados, dos backups e dos logs. Em produção, esse arquivo pertence ao usuário dedicado da aplicação e não permite acesso de grupo ou de outros usuários.

O Manager pode manter no SQLite metadados operacionais dos backups, como nome gerenciado, tamanho, SHA-256, localização, estado e timestamps. Credenciais e tokens OAuth não são armazenados no SQLite.

## Compartilhamento

O Palworld Manager não vende nem compartilha dados do Google com terceiros. A comunicação necessária ocorre diretamente entre a instalação auto-hospedada, o rclone e os serviços do Google usados para executar as operações autorizadas.

## Retenção e exclusão

Os backups remotos permanecem no Google Drive conforme a retenção configurada pelo administrador, inicialmente limitada aos 10 backups gerenciados mais recentes. O administrador pode solicitar a exclusão de um backup gerenciado. Arquivos externos ao namespace do Palworld Manager são preservados.

O token OAuth permanece disponível enquanto a integração estiver configurada. O administrador pode revogar o acesso nas configurações de segurança da conta Google e remover a configuração local do rclone.

## Segurança

A aplicação executa o rclone sem shell, com argumentos controlados, timeouts e prompts desabilitados. Tokens, credenciais, configuração bruta e erros externos não são exibidos na interface, auditoria ou diagnósticos.

## Contato

Dúvidas sobre esta política devem ser enviadas ao e-mail de suporte exibido na tela de consentimento OAuth do Palworld Manager ou ao administrador responsável pela instalação.

[Voltar à documentação](index.md)
