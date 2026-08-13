# Segurança

> Status: Em desenvolvimento. Credenciais Argon2id, sessões server-side, cookies e CSRF estão implementados; brute force e auditoria de login permanecem planejados para a etapa seguinte.

A aplicação seguirá o princípio do menor privilégio. Em produção, será executada pelo usuário Linux dedicado `palmanager`, nunca como `root`. O `sudoers` permitirá somente comandos ou scripts estritamente necessários, com executáveis, serviços, caminhos e argumentos validados; não haverá permissão genérica.

## Acesso e autenticação

- FastAPI escutará apenas em localhost e será publicado por Tailscale Serve com HTTPS.
- Tailscale controlará os dispositivos autorizados, sem whitelist duplicada no Manager.
- O Manager armazena somente hashes Argon2id. A senha deve ter ao menos 6 caracteres e nunca é aceita como argumento de linha de comando.
- A criação do administrador inicial e a redefinição de senha estão disponíveis por CLI interativa, com entrada oculta e confirmação.
- As sessões são server-side no SQLite. Tokens de sessão e CSRF são aleatórios; somente seus hashes ficam no banco.
- O cookie contém um identificador opaco. Sessões duram no máximo 8 horas e expiram após 1 hora de inatividade; logout e troca de senha as revogam.
- Cookies de autenticação usam `HttpOnly` e `SameSite=Strict`. `Secure` é obrigatório em produção e omitido somente em development/test para permitir HTTP local.
- Cinco tentativas inválidas causarão bloqueio por 15 minutos e serão auditadas.
- Login e logout já validam CSRF; toda nova ação que alterar estado deverá aplicar a mesma proteção e controles contra double-submit.

## Secrets e registros

Secrets ficarão fora do SQLite, em arquivo de ambiente com acesso restrito em produção. Senhas, tokens, webhooks, cookies e credenciais não podem aparecer completos na interface, em logs, auditorias, fixtures, backups ou diagnósticos. Logs devem mascarar valores sensíveis e evitar registrar headers ou ambientes indiscriminadamente.

A configuração estrutural já é validada com Pydantic Settings no startup de web e worker. Erros de validação ocultam os valores recebidos, e o ambiente `production` rejeita `APP_HOST` que não seja loopback. O carregamento de secrets reais será implementado junto às integrações e ao deploy, sempre por variáveis de processo provenientes do arquivo protegido previsto para produção.

## Comandos, caminhos e arquivos

- Usar chamadas de processo com argumentos separados e `shell=False`; evitar `shell=True`.
- Aceitar somente comandos, serviços e caminhos previamente permitidos.
- Normalizar e validar caminhos contra path traversal e acesso por symlink.
- Ao criar ou extrair `.tar.gz`, rejeitar caminhos absolutos, `..`, links perigosos e conteúdo fora do destino autorizado.
- Validar formato, tamanho e integridade antes de usar um backup.

Operações destrutivas exigirão confirmações explícitas, locks e auditoria. Uma operação interrompida não será retomada automaticamente. Os requisitos completos estão em [SPECIFICATION.md](../../SPECIFICATION.md), especialmente nas seções de autenticação, jobs, backup e hardening.
