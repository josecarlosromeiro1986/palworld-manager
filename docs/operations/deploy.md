# Deploy recorrente e rollback

> Status: Implementado na Etapa 30. O rollback é sempre manual e explícito;
> nenhuma falha de deploy troca o commit automaticamente.

Este runbook atualiza uma instalação nativa criada conforme a
[instalação em produção](production-install.md). Docker não participa do deploy
no host. O script versionado `deploy.sh` instala sua cópia estável em
`/usr/local/sbin/palworld-manager-deploy`.

## Contrato do script

O deploy usa paths e identidades fechados:

```text
checkout:        /opt/palworld-manager
dados:           /var/lib/palworld-manager
estado:          /var/lib/palworld-manager/deploy/previous-commit
web:             palworld-manager.service
worker:          palworld-manager-worker.service
usuário runtime: palmanager
alvo normal:     origin/develop
```

O orquestrador exige root porque atualiza o checkout root-owned, arquivos em
`/etc`, units e serviços. Build, dependências, gate, configuração, migrations,
web e worker continuam executados como `palmanager`; a aplicação nunca roda
como root.

O executável `.venv/bin/python` criado pelo módulo `venv` normalmente é um
symlink. O deploy aceita essa estrutura somente quando a venv e seu diretório
`bin/` pertencem a root, não permitem escrita de grupo/outros e o link resolve
para um arquivo regular executável também pertencente a root e sem escrita de
grupo/outros. Link quebrado, diretório substituído por symlink ou destino
gravável continua bloqueando o deploy antes de qualquer serviço ser parado.
O destino resolvido também precisa executar Python 3.12 ou superior. Esse mesmo
executável protegido cria a venv isolada do gate; o `python3` padrão da
distribuição não é usado e, portanto, não pode rebaixar silenciosamente o
staging para uma versão incompatível.

O script:

1. adquire um lock exclusivo em `/run/lock`;
2. exige checkout limpo, paths regulares e arquivos protegidos;
3. atualiza somente `origin/develop` e recusa histórico que não seja
   fast-forward;
4. confirma que o commit alvo reconhece a revisão Alembic atual;
5. cria um worktree temporário dentro da área administrada;
6. instala dependências, compila assets e executa `make check` como
   `palmanager`, com `APP_ENVIRONMENT=test`;
7. valida sudoers e units antes de tocar nos serviços;
8. recusa interromper job `RUNNING`, notificação `SENDING` ou maintenance lock;
9. para web e worker de forma separada;
10. grava atomicamente o commit anterior com modo `0640`;
11. ativa o candidato, instala dependências/assets/configuração e normaliza
    ownership e permissões;
12. valida a configuração e aplica `alembic upgrade head` por transient
    services não-root com os EnvironmentFiles protegidos;
13. reinicia e valida web e worker separadamente.

O staging é removido ao sair. Essa limpeza não altera o commit ativo, não executa
rollback e não reinicia serviços.

## Instalar o comando estável

Instalações novas já executam este passo no runbook de produção:

```bash
cd /opt/palworld-manager
sudo install -o root -g root -m 0750 deploy.sh /usr/local/sbin/palworld-manager-deploy
```

Para atualizar uma instalação da Etapa 29 que ainda não possui `deploy.sh`,
extraia somente o script do ref remoto, sem alterar o checkout:

```bash
sudo /usr/bin/git -C /opt/palworld-manager fetch --prune -- origin refs/heads/develop:refs/remotes/origin/develop
temporary_deploy="$(mktemp)"
sudo /usr/bin/git -C /opt/palworld-manager show origin/develop:deploy.sh >"${temporary_deploy}"
sudo install -o root -g root -m 0750 "${temporary_deploy}" /usr/local/sbin/palworld-manager-deploy
rm -- "${temporary_deploy}"
```

Revise o hash de `origin/develop` e a procedência do remote antes de executar o
comando extraído. O primeiro deploy bem-sucedido atualiza a cópia estável.

## Executar deploy

Confirme previamente que não há manutenção em andamento e execute:

```bash
sudo /usr/local/sbin/palworld-manager-deploy deploy
```

Não use variáveis de ambiente para substituir paths, serviços, comandos ou ref.
O script não aceita alvo arbitrário no modo `deploy`.

O sucesso termina com o commit alvo e o path do registro anterior. Confirme:

```bash
sudo stat -c '%U %G %a %n' /var/lib/palworld-manager/deploy/previous-commit
sudo /usr/bin/git -C /opt/palworld-manager rev-parse HEAD
systemctl is-active --quiet palworld-manager.service
systemctl is-active --quiet palworld-manager-worker.service
```

O arquivo deve ser `root:palmanager 640`. Ele contém apenas um SHA-1 completo,
nunca secret ou configuração.

## Validação independente

A web passa somente quando:

- `palworld-manager.service` está ativo;
- a propriedade `User` permanece `palmanager`;
- `http://127.0.0.1:8080/health` retorna exatamente `{"status":"ok"}`.

O worker passa somente quando:

- `palworld-manager-worker.service` está ativo;
- a propriedade `User` permanece `palmanager`;
- existe heartbeat `PRIMARY` iniciado pelo novo processo e tanto `started_at`
  quanto `heartbeat_at` têm menos de 30 segundos.

O worker não publica HTTP. Um `/health` válido nunca substitui a validação do
heartbeat, e um heartbeat válido nunca substitui a validação da web.

## Falha de deploy

O script nunca executa rollback, `alembic downgrade` ou restart compensatório
automaticamente. Se a falha ocorrer antes de parar os serviços, o checkout ativo
permanece intacto. Se ocorrer depois, web e worker podem permanecer parados para
evitar executar uma versão parcial.

Preserve a saída sem adicionar `set -x` e consulte:

```bash
systemctl status palworld-manager.service --no-pager
systemctl status palworld-manager-worker.service --no-pager
journalctl --unit palworld-manager.service --unit palworld-manager-worker.service --lines 100 --no-pager
sudo /usr/bin/git -C /opt/palworld-manager status --short --branch
sudo stat -c '%U %G %a %n' /etc/palworld-manager/secrets.env /var/lib/palworld-manager/deploy/previous-commit
```

Não mostre conteúdo de EnvironmentFiles, rclone ou secrets. Não inicie serviços
se migrations, configuração ou ownership permanecerem incertos.

## Rollback manual

Rollback só é permitido para o SHA completo registrado pelo último deploy:

```bash
previous_commit="$(sudo /usr/bin/head --lines=1 /var/lib/palworld-manager/deploy/previous-commit)"
printf '%s\n' "${previous_commit}"
sudo /usr/local/sbin/palworld-manager-deploy rollback "${previous_commit}"
```

Antes de confirmar:

- verifique que o SHA é o commit esperado;
- confirme que não há job, notificação ou maintenance lock em execução;
- preserve diagnóstico e saída do deploy que falhou;
- entenda se o deploy aplicou migration nova.

O script compara a revisão em `alembic_version` com as migrations presentes no
commit anterior. Se o alvo não reconhecer a revisão atual, o rollback é
bloqueado antes de parar serviços. Não use `alembic downgrade`, não edite
`alembic_version` e não restaure banco automaticamente. Nesse caso, escolha uma
correção forward compatível ou um procedimento offline de recuperação do banco
avaliado especificamente para o incidente.

Um rollback aprovado percorre novamente dependências, assets, gate, config,
migrations compatíveis, restart e as duas validações. Ao concluir, o registro
passa a conter o commit do qual o rollback saiu, permitindo apenas uma nova ação
humana explícita.

Consulte também [Segurança](../architecture/security.md),
[Jobs e locks](../architecture/jobs-and-locks.md) e
[Troubleshooting](troubleshooting.md).
