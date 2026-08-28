#!/usr/bin/env bash

set -Eeuo pipefail
umask 027

readonly APP_DIR="/opt/palworld-manager"
readonly SERVICE_USER="palmanager"
readonly SERVICE_GROUP="palmanager"
readonly WEB_SERVICE="palworld-manager.service"
readonly WORKER_SERVICE="palworld-manager-worker.service"
readonly MANAGER_ENV="/etc/palworld-manager/manager.env"
readonly SECRETS_ENV="/etc/palworld-manager/secrets.env"
readonly DATABASE="/var/lib/palworld-manager/manager.db"
readonly STATE_DIR="/var/lib/palworld-manager/deploy"
readonly PREVIOUS_COMMIT_FILE="${STATE_DIR}/previous-commit"
readonly TMP_ROOT="/var/lib/palworld-manager/tmp"
readonly LOCK_FILE="/run/lock/palworld-manager-deploy.lock"
readonly STABLE_COMMAND="/usr/local/sbin/palworld-manager-deploy"

MODE="deploy"
ROLLBACK_COMMIT=""
TARGET_COMMIT=""
CURRENT_COMMIT=""
STAGING_DIR=""
WORKTREE_DIR=""
VENV_PYTHON_RESOLVED=""

log() {
    printf '[palworld-manager-deploy] %s\n' "$*"
}

die() {
    printf '[palworld-manager-deploy] ERRO: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Uso:
  deploy.sh deploy
  deploy.sh rollback <commit-anterior-completo>

O deploy atualiza para origin/develop. O rollback exige o SHA-1 completo salvo
no último deploy e nunca é executado automaticamente.
EOF
}

cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "${WORKTREE_DIR}" ]]; then
        /usr/bin/git -C "${APP_DIR}" worktree remove --force -- "${WORKTREE_DIR}" \
            >/dev/null 2>&1 || true
    fi
    if [[ -n "${STAGING_DIR}" \
        && "${STAGING_DIR}" == "${TMP_ROOT}"/deploy.* \
        && -d "${STAGING_DIR}" \
        && ! -L "${STAGING_DIR}" ]]; then
        /bin/rm -rf -- "${STAGING_DIR}"
    fi
    exit "${status}"
}

trap cleanup EXIT

parse_arguments() {
    case "${1:-deploy}" in
        deploy)
            [[ $# -le 1 ]] || die "deploy não aceita argumentos adicionais"
            MODE="deploy"
            ;;
        rollback)
            [[ $# -eq 2 ]] || die "rollback exige o commit anterior completo"
            [[ "$2" =~ ^[0-9a-fA-F]{40}$ ]] \
                || die "o commit de rollback deve ser um SHA-1 completo"
            MODE="rollback"
            ROLLBACK_COMMIT="${2,,}"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "ação inválida"
            ;;
    esac
}

require_root() {
    [[ ${EUID} -eq 0 ]] || die "execute este script como root via sudo"
}

require_commands() {
    local command
    local -a commands=(
        /bin/chmod
        /bin/chown
        /bin/cp
        /bin/mv
        /bin/rm
        /usr/bin/curl
        /usr/bin/find
        /usr/bin/flock
        /usr/bin/git
        /usr/bin/install
        /usr/bin/make
        /usr/bin/mktemp
        /usr/bin/npm
        /usr/bin/readlink
        /usr/bin/sleep
        /usr/bin/sqlite3
        /usr/bin/stat
        /usr/bin/systemctl
        /usr/bin/systemd-analyze
        /usr/bin/systemd-run
        /usr/bin/systemd-tmpfiles
        /usr/sbin/runuser
        /usr/sbin/visudo
    )
    for command in "${commands[@]}"; do
        [[ -x "${command}" ]] || die "executável obrigatório ausente: ${command}"
    done
}

require_regular_file() {
    local path=$1
    [[ -f "${path}" && ! -L "${path}" ]] || die "arquivo obrigatório inválido: ${path}"
}

validate_root_protected_mode() {
    local path=$1
    local owner
    local mode
    owner=$(/usr/bin/stat --format='%u' -- "${path}") || return 1
    mode=$(/usr/bin/stat --format='%a' -- "${path}") || return 1
    [[ "${owner}" == "0" && "${mode}" =~ ^[0-7]{3,4}$ ]] || return 1
    (( (8#${mode} & 8#022) == 0 ))
}

validate_venv_python() {
    local venv="${APP_DIR}/.venv"
    local python_path="${venv}/bin/python"
    local resolved
    [[ -d "${venv}" && ! -L "${venv}" ]] \
        || die "venv de produção inválido"
    [[ -d "${venv}/bin" && ! -L "${venv}/bin" ]] \
        || die "diretório de executáveis da venv inválido"
    validate_root_protected_mode "${venv}" \
        && validate_root_protected_mode "${venv}/bin" \
        || die "venv de produção não está protegida contra escrita"
    [[ -x "${python_path}" ]] || die "Python da venv não está disponível"
    resolved=$(
        /usr/bin/readlink --canonicalize-existing -- "${python_path}"
    ) || die "Python da venv possui destino inválido"
    [[ -f "${resolved}" && -x "${resolved}" ]] \
        || die "Python da venv não resolve para executável regular"
    validate_root_protected_mode "${resolved}" \
        || die "Python da venv não está protegido contra escrita"
    "${resolved}" -I -S -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
        || die "Python da venv precisa ser 3.12 ou superior"
    VENV_PYTHON_RESOLVED="${resolved}"
}

validate_host_layout() {
    [[ -d "${APP_DIR}" && ! -L "${APP_DIR}" ]] \
        || die "diretório da aplicação inválido"
    [[ -d "${APP_DIR}/.git" && ! -L "${APP_DIR}/.git" ]] \
        || die "checkout Git inválido"
    require_regular_file "${MANAGER_ENV}"
    require_regular_file "${SECRETS_ENV}"
    require_regular_file "${DATABASE}"
    require_regular_file "${APP_DIR}/pyproject.toml"
    require_regular_file "${APP_DIR}/package-lock.json"
    validate_venv_python

    local changes
    changes=$(/usr/bin/git -C "${APP_DIR}" status --porcelain --untracked-files=all)
    [[ -z "${changes}" ]] \
        || die "o checkout de produção possui alterações; revise-o manualmente"
}

initialize_runtime_paths() {
    /usr/bin/install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${STATE_DIR}"
    /usr/bin/install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0700 "${TMP_ROOT}"
    exec 9>"${LOCK_FILE}"
    /usr/bin/flock --nonblock 9 || die "já existe outro deploy em execução"
}

select_target_commit() {
    CURRENT_COMMIT=$(
        /usr/bin/git -C "${APP_DIR}" rev-parse --verify --end-of-options 'HEAD^{commit}'
    )
    if [[ "${MODE}" == "deploy" ]]; then
        log "Atualizando referências de origin/develop"
        /usr/bin/git -C "${APP_DIR}" fetch --prune -- origin \
            refs/heads/develop:refs/remotes/origin/develop
        TARGET_COMMIT=$(
            /usr/bin/git -C "${APP_DIR}" rev-parse --verify --end-of-options \
                'origin/develop^{commit}'
        )
        /usr/bin/git -C "${APP_DIR}" merge-base --is-ancestor \
            "${CURRENT_COMMIT}" "${TARGET_COMMIT}" \
            || die "origin/develop não é avanço do commit atual; use rollback explícito"
    else
        require_regular_file "${PREVIOUS_COMMIT_FILE}"
        local recorded
        IFS= read -r recorded <"${PREVIOUS_COMMIT_FILE}"
        [[ "${recorded}" =~ ^[0-9a-f]{40}$ ]] \
            || die "registro do commit anterior é inválido"
        [[ "${ROLLBACK_COMMIT}" == "${recorded}" ]] \
            || die "rollback permitido somente para o commit anterior registrado"
        /usr/bin/git -C "${APP_DIR}" cat-file -e "${ROLLBACK_COMMIT}^{commit}" \
            || die "commit anterior não está disponível no repositório local"
        TARGET_COMMIT="${ROLLBACK_COMMIT}"
    fi
    log "Commit atual: ${CURRENT_COMMIT}"
    log "Commit alvo: ${TARGET_COMMIT}"
}

database_revision() {
    local revision
    revision=$(
        /usr/bin/sqlite3 -readonly "${DATABASE}" \
            'SELECT version_num FROM alembic_version ORDER BY version_num;'
    ) || die "não foi possível ler a revisão Alembic atual"
    [[ "${revision}" =~ ^[A-Za-z0-9_]+$ ]] \
        || die "o banco não possui uma única revisão Alembic válida"
    printf '%s\n' "${revision}"
}

validate_migration_compatibility() {
    local revision
    local revision_marker
    revision=$(database_revision)
    printf -v revision_marker 'revision: str = "%s"' "${revision}"
    /usr/bin/git -C "${APP_DIR}" grep --quiet --fixed-strings \
        "${revision_marker}" "${TARGET_COMMIT}" -- migrations/versions \
        || die "o commit alvo não reconhece a revisão Alembic ${revision}; rollback bloqueado"
}

prepare_candidate() {
    STAGING_DIR=$(/usr/bin/mktemp -d --tmpdir="${TMP_ROOT}" deploy.XXXXXXXX)
    [[ "${STAGING_DIR}" == "${TMP_ROOT}"/deploy.* \
        && -d "${STAGING_DIR}" \
        && ! -L "${STAGING_DIR}" ]] \
        || die "staging de deploy inválido"
    WORKTREE_DIR="${STAGING_DIR}/source"
    /usr/bin/git -C "${APP_DIR}" worktree add --detach -- "${WORKTREE_DIR}" \
        "${TARGET_COMMIT}"
    /bin/chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${STAGING_DIR}"
    /usr/bin/install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0700 \
        "${STAGING_DIR}/home" "${STAGING_DIR}/npm-cache"
}

run_as_manager() {
    /usr/sbin/runuser --user "${SERVICE_USER}" -- "$@"
}

validate_candidate_artifacts() {
    require_regular_file "${WORKTREE_DIR}/ops/environment/manager.env"
    require_regular_file "${WORKTREE_DIR}/ops/sudoers/palworld-manager"
    require_regular_file "${WORKTREE_DIR}/ops/systemd/palworld-manager.service"
    require_regular_file "${WORKTREE_DIR}/ops/systemd/palworld-manager-worker.service"
    require_regular_file "${WORKTREE_DIR}/ops/tmpfiles/palworld-manager.conf"
    /usr/sbin/visudo --check --file \
        "${WORKTREE_DIR}/ops/sudoers/palworld-manager"
    /usr/bin/systemd-analyze verify \
        "${WORKTREE_DIR}/ops/systemd/palworld-manager.service" \
        "${WORKTREE_DIR}/ops/systemd/palworld-manager-worker.service"
}

check_candidate() {
    local check_venv="${STAGING_DIR}/check-venv"
    log "Instalando e validando o candidato isolado como ${SERVICE_USER}"
    [[ -n "${VENV_PYTHON_RESOLVED}" ]] \
        || die "Python validado da venv não está disponível"
    run_as_manager "${VENV_PYTHON_RESOLVED}" -I -S -m venv "${check_venv}"
    run_as_manager /usr/bin/env HOME="${STAGING_DIR}/home" \
        "${check_venv}/bin/python" -m pip install \
        --disable-pip-version-check --no-input "${WORKTREE_DIR}[dev]"
    run_as_manager /usr/bin/env HOME="${STAGING_DIR}/home" \
        /usr/bin/npm ci --prefix "${WORKTREE_DIR}" \
        --cache "${STAGING_DIR}/npm-cache"
    run_as_manager /usr/bin/env \
        HOME="${STAGING_DIR}/home" \
        IN_CONTAINER=1 \
        APP_ENVIRONMENT=test \
        PATH="${check_venv}/bin:/usr/bin:/bin" \
        /usr/bin/make -C "${WORKTREE_DIR}" check
    [[ -d "${WORKTREE_DIR}/app/static/dist" \
        && ! -L "${WORKTREE_DIR}/app/static/dist" ]] \
        || die "o build não produziu os assets esperados"
}

active_job_count() {
    /usr/bin/sqlite3 -readonly "${DATABASE}" \
        "SELECT COUNT(*) FROM jobs WHERE status = 'RUNNING';"
}

sending_notification_count() {
    /usr/bin/sqlite3 -readonly "${DATABASE}" \
        "SELECT COUNT(*) FROM notification_events WHERE status = 'SENDING';"
}

maintenance_lock_count() {
    /usr/bin/sqlite3 -readonly "${DATABASE}" \
        'SELECT COUNT(*) FROM maintenance_locks;'
}

assert_worker_can_stop() {
    [[ "$(active_job_count)" == "0" ]] \
        || die "há job em execução; o deploy não interromperá o worker"
    [[ "$(sending_notification_count)" == "0" ]] \
        || die "há notificação em entrega; o deploy não interromperá o worker"
    [[ "$(maintenance_lock_count)" == "0" ]] \
        || die "há maintenance lock ativo; o deploy não interromperá o worker"
}

stop_services() {
    assert_worker_can_stop
    log "Parando a web antes de alterar o checkout"
    /usr/bin/systemctl stop "${WEB_SERVICE}"
    assert_worker_can_stop
    log "Parando o worker de forma graciosa"
    /usr/bin/systemctl stop "${WORKER_SERVICE}"
    /usr/bin/systemctl is-active --quiet "${WORKER_SERVICE}" \
        && die "o worker permaneceu ativo"
    assert_worker_can_stop
}

record_previous_commit() {
    local temporary="${STATE_DIR}/.previous-commit.tmp"
    printf '%s\n' "${CURRENT_COMMIT}" >"${temporary}"
    /bin/chown root:"${SERVICE_GROUP}" "${temporary}"
    /bin/chmod 0640 "${temporary}"
    /bin/mv -- "${temporary}" "${PREVIOUS_COMMIT_FILE}"
}

replace_assets() {
    local static_root="${APP_DIR}/app/static"
    local next_assets="${static_root}/.dist-deploy-next"
    local old_assets="${static_root}/.dist-deploy-previous"
    [[ -d "${static_root}" && ! -L "${static_root}" ]] \
        || die "diretório estático inválido"
    /bin/rm -rf -- "${next_assets}" "${old_assets}"
    /bin/cp -a -- "${WORKTREE_DIR}/app/static/dist" "${next_assets}"
    /bin/chown -R root:"${SERVICE_GROUP}" "${next_assets}"
    /usr/bin/find "${next_assets}" -type d -exec /bin/chmod 0755 {} +
    /usr/bin/find "${next_assets}" -type f -exec /bin/chmod 0644 {} +
    if [[ -d "${static_root}/dist" && ! -L "${static_root}/dist" ]]; then
        /bin/mv -- "${static_root}/dist" "${old_assets}"
    elif [[ -e "${static_root}/dist" || -L "${static_root}/dist" ]]; then
        die "destino dos assets é inválido"
    fi
    /bin/mv -- "${next_assets}" "${static_root}/dist"
    /bin/rm -rf -- "${old_assets}"
}

install_runtime_dependencies() {
    local venv="${APP_DIR}/.venv"
    [[ -d "${venv}" && ! -L "${venv}" ]] || die "venv inválido"
    /bin/chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${venv}"
    run_as_manager /usr/bin/env HOME="${STAGING_DIR}/home" \
        "${venv}/bin/python" -m pip install \
        --disable-pip-version-check --no-input --upgrade "${WORKTREE_DIR}"
    /bin/chown -R root:"${SERVICE_GROUP}" "${venv}"
    /bin/chmod -R g-w,o-rwx "${venv}"
    /usr/bin/find "${venv}" -type d -exec /bin/chmod g+rx {} +
    /usr/bin/find "${venv}" -type f -exec /bin/chmod g+r {} +
}

normalize_application_permissions() {
    /bin/chown -R root:"${SERVICE_GROUP}" "${APP_DIR}"
    /bin/chmod -R g-w,o-rwx "${APP_DIR}"
    /usr/bin/find "${APP_DIR}" -type d -exec /bin/chmod g+rx {} +
    /usr/bin/find "${APP_DIR}" -type f -exec /bin/chmod g+r {} +
}

activate_candidate() {
    record_previous_commit
    /usr/bin/git -C "${APP_DIR}" checkout --detach "${TARGET_COMMIT}"
    [[ "$(/usr/bin/git -C "${APP_DIR}" rev-parse HEAD)" == "${TARGET_COMMIT}" ]] \
        || die "checkout não confirmou o commit alvo"
    replace_assets
    install_runtime_dependencies
    normalize_application_permissions
}

install_operational_files() {
    /usr/bin/install -o root -g "${SERVICE_GROUP}" -m 0640 \
        "${APP_DIR}/ops/environment/manager.env" "${MANAGER_ENV}"
    /usr/bin/install -o root -g root -m 0440 \
        "${APP_DIR}/ops/sudoers/palworld-manager" \
        /etc/sudoers.d/palworld-manager
    /usr/sbin/visudo --check --file /etc/sudoers.d/palworld-manager
    /usr/bin/install -o root -g root -m 0644 \
        "${APP_DIR}/ops/systemd/palworld-manager.service" \
        /etc/systemd/system/palworld-manager.service
    /usr/bin/install -o root -g root -m 0644 \
        "${APP_DIR}/ops/systemd/palworld-manager-worker.service" \
        /etc/systemd/system/palworld-manager-worker.service
    /usr/bin/install -o root -g root -m 0644 \
        "${APP_DIR}/ops/tmpfiles/palworld-manager.conf" \
        /etc/tmpfiles.d/palworld-manager.conf
    /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/palworld-manager.conf
    /usr/bin/systemctl daemon-reload
    /usr/bin/systemd-analyze verify \
        /etc/systemd/system/palworld-manager.service \
        /etc/systemd/system/palworld-manager-worker.service
    if [[ -f "${APP_DIR}/deploy.sh" && ! -L "${APP_DIR}/deploy.sh" ]]; then
        /usr/bin/install -o root -g root -m 0750 \
            "${APP_DIR}/deploy.sh" "${STABLE_COMMAND}"
    fi
}

run_transient() {
    local unit=$1
    shift
    /usr/bin/systemd-run --quiet --wait --pipe --collect \
        --unit="${unit}" \
        --property=Type=oneshot \
        --property=User="${SERVICE_USER}" \
        --property=Group="${SERVICE_GROUP}" \
        --property=WorkingDirectory="${APP_DIR}" \
        --property=EnvironmentFile="${MANAGER_ENV}" \
        --property=EnvironmentFile="${SECRETS_ENV}" \
        --property=NoNewPrivileges=yes \
        --property=ProtectSystem=strict \
        --property=ReadWritePaths=/var/lib/palworld-manager \
        "$@"
}

validate_runtime_configuration() {
    run_transient "palworld-manager-config-${BASHPID}" \
        "${APP_DIR}/.venv/bin/python" -c \
        'from app.config import Settings; Settings()'
}

run_migrations() {
    run_transient "palworld-manager-migrate-${BASHPID}" \
        "${APP_DIR}/.venv/bin/alembic" upgrade head
}

restart_services() {
    /usr/bin/systemctl restart "${WEB_SERVICE}"
    /usr/bin/systemctl restart "${WORKER_SERVICE}"
}

validate_web() {
    [[ "$(/usr/bin/systemctl show --property=User --value "${WEB_SERVICE}")" \
        == "${SERVICE_USER}" ]] || die "a web não está configurada como palmanager"
    local attempt
    local health
    for ((attempt = 1; attempt <= 30; attempt++)); do
        if /usr/bin/systemctl is-active --quiet "${WEB_SERVICE}"; then
            health=$(
                /usr/bin/curl --fail --silent --show-error --max-time 2 \
                    http://127.0.0.1:8080/health 2>/dev/null || true
            )
            [[ "${health}" == '{"status":"ok"}' ]] && return 0
        fi
        /usr/bin/sleep 1
    done
    die "a web não passou na validação systemd + /health"
}

worker_heartbeat_state() {
    /usr/bin/sqlite3 -readonly "${DATABASE}" <<'SQL'
SELECT COALESCE((
    SELECT CASE
        WHEN (julianday('now') - julianday(started_at)) * 86400.0 BETWEEN 0.0 AND 30.0
         AND (julianday('now') - julianday(heartbeat_at)) * 86400.0 BETWEEN 0.0 AND 30.0
        THEN 'HEALTHY'
        ELSE 'WAITING'
    END
    FROM worker_heartbeats
    WHERE key = 'PRIMARY'
), 'WAITING');
SQL
}

validate_worker() {
    [[ "$(/usr/bin/systemctl show --property=User --value "${WORKER_SERVICE}")" \
        == "${SERVICE_USER}" ]] || die "o worker não está configurado como palmanager"
    local attempt
    for ((attempt = 1; attempt <= 40; attempt++)); do
        if /usr/bin/systemctl is-active --quiet "${WORKER_SERVICE}" \
            && [[ "$(worker_heartbeat_state)" == "HEALTHY" ]]; then
            return 0
        fi
        /usr/bin/sleep 1
    done
    die "o worker não passou na validação systemd + heartbeat"
}

main() {
    parse_arguments "$@"
    require_root
    require_commands
    validate_host_layout
    initialize_runtime_paths
    select_target_commit
    validate_migration_compatibility
    prepare_candidate
    validate_candidate_artifacts
    check_candidate
    stop_services
    activate_candidate
    install_operational_files
    validate_runtime_configuration
    run_migrations
    restart_services
    validate_web
    validate_worker
    log "${MODE} concluído em ${TARGET_COMMIT}"
    log "Commit anterior registrado em ${PREVIOUS_COMMIT_FILE}"
}

main "$@"
