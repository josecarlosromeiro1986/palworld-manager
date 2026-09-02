# Usuários e controle de acesso

> Status: Implementado na Etapa 32, após a V1 `1.0.0`.

O Palworld Manager possui dois papéis locais: `ADMIN` e `USER`. Autenticação,
sessões e autorização permanecem no SQLite; não há dependência de um provedor
externo. O administrador criado pela CLI durante a instalação é migrado para
`ADMIN` sem alterar username, senha ou sessões.

## Matriz de acesso

| Recurso | `ADMIN` | `USER` |
| --- | --- | --- |
| Dashboard e health resumido | Sim | Sim |
| Start e Restart do Palworld | Sim | Sim |
| Stop assistido 0/1/5/10 min | Sim | Sim |
| Cancelar/antecipar Stop assistido | Qualquer job | Somente o próprio job |
| Minha conta e troca da própria senha | Sim | Sim |
| Backup rápido, backups e restore | Sim | Não |
| SIGTERM e SIGKILL | Sim | Não |
| Reboot e shutdown do Ubuntu | Sim | Não |
| Jogadores, logs, updates, configurações, diagnóstico e auditoria | Sim | Não |
| Gestão de usuários | Sim | Não |

As opções não autorizadas são removidas da interface, mas isso não é a
fronteira de segurança. O middleware aplica uma allowlist fechada no backend e
responde HTTP 403 a qualquer tentativa direta do `USER` fora das rotas
permitidas.

## Criar e administrar contas

A página **Usuários** está disponível somente para `ADMIN`. Ela permite:

- criar uma conta `ADMIN` ou `USER`;
- alterar o papel;
- ativar ou desativar;
- definir uma nova senha temporária.

Contas nunca são excluídas fisicamente. O username é imutável, tem no máximo
100 caracteres, rejeita vazio e quebras de linha, preserva a grafia original
para exibição e usa comparação Unicode sem distinção entre maiúsculas e
minúsculas para login e unicidade.

Alteração de papel, alteração de status ou reset administrativo revoga todas as
sessões do alvo. Reativar uma conta preserva a senha existente. Um
administrador não pode alterar o próprio papel/status nem redefinir a própria
senha nessa página; a senha própria pertence a **Minha conta**. O último
administrador ativo não pode ser desativado nem rebaixado.

## Senha temporária e Minha conta

Toda conta criada pela UI recebe uma senha temporária definida pelo
administrador. No primeiro login, o usuário é direcionado para **Minha conta**;
até concluir a troca, somente essa página e logout são aceitos. A troca exige:

1. senha atual;
2. nova senha compatível com a política;
3. confirmação exata da nova senha.

O sucesso grava somente o hash Argon2id, remove a pendência, revoga todas as
sessões — inclusive a atual —, limpa os cookies e retorna ao login. A senha
temporária, a nova senha e seus hashes nunca aparecem em logs, auditoria,
respostas ou documentação.

## Autoria de jobs

A revision `0007_user_roles_access_control` adiciona
`jobs.requested_by_user_id`. Start, Restart, Stop assistido e escaladas
administrativas registram o solicitante. Durante a contagem, a atualização
condicional de cancelamento ou execução imediata inclui o proprietário quando o
ator é `USER`; para `ADMIN`, a mesma operação pode atingir qualquer job válido.

## Auditoria

Criação, mudança de papel, mudança de status, reset administrativo e troca da
própria senha geram eventos sem valores sensíveis. Os eventos registram ator e
alvo; detalhes ficam limitados ao papel anterior/novo ou ao status.

Consulte também [Segurança](../architecture/security.md),
[Modelo de dados](../architecture/data-model.md) e
[Controle do servidor](server-lifecycle.md).
