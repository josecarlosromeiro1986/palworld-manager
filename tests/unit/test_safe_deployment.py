import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
DEPLOY_SCRIPT = PROJECT_ROOT / "deploy.sh"


def _script() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def test_deploy_script_has_valid_bash_syntax_and_safe_help() -> None:
    syntax = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr

    help_result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "rollback <commit-anterior-completo>" in help_result.stdout


def test_deploy_uses_fixed_production_paths_root_guard_and_lock() -> None:
    script = _script()

    assert 'readonly APP_DIR="/opt/palworld-manager"' in script
    assert 'readonly SERVICE_USER="palmanager"' in script
    assert 'readonly LOCK_FILE="/run/lock/palworld-manager-deploy.lock"' in script
    assert "[[ ${EUID} -eq 0 ]]" in script
    assert "/usr/bin/flock --nonblock 9" in script
    assert "status --porcelain --untracked-files=all" in script
    assert "eval " not in script
    assert "set -x" not in script


def test_candidate_runs_dependencies_assets_and_gate_as_palmanager() -> None:
    script = _script()

    assert 'run_as_manager /usr/bin/python3 -m venv "${check_venv}"' in script
    assert "/usr/bin/npm ci --prefix" in script
    assert '/usr/bin/make -C "${WORKTREE_DIR}" check' in script
    assert "APP_ENVIRONMENT=test" in script
    assert '/bin/chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${venv}"' in script
    assert 'run_as_manager /usr/bin/env HOME="${STAGING_DIR}/home"' in script


def test_rollback_is_explicit_recorded_and_migration_compatible() -> None:
    script = _script()

    assert 'MODE="rollback"' in script
    assert '[[ "${ROLLBACK_COMMIT}" == "${recorded}" ]]' in script
    assert "rollback permitido somente para o commit anterior registrado" in script
    assert 'revision: str = "%s"' in script
    assert "rollback bloqueado" in script
    cleanup = script.split("cleanup() {", maxsplit=1)[1].split("parse_arguments() {", maxsplit=1)[0]
    assert "rollback" not in cleanup.casefold()
    assert "checkout" not in cleanup.casefold()


def test_deploy_refuses_to_interrupt_running_worker_work() -> None:
    script = _script()

    assert "WHERE status = 'RUNNING'" in script
    assert "WHERE status = 'SENDING'" in script
    assert "SELECT COUNT(*) FROM maintenance_locks;" in script
    assert "há job em execução" in script
    assert "há notificação em entrega" in script
    assert "há maintenance lock ativo" in script
    assert script.index('systemctl stop "${WEB_SERVICE}"') < script.index(
        'systemctl stop "${WORKER_SERVICE}"'
    )


def test_migrations_and_config_use_protected_transient_service() -> None:
    script = _script()

    assert '--property=User="${SERVICE_USER}"' in script
    assert '--property=EnvironmentFile="${MANAGER_ENV}"' in script
    assert '--property=EnvironmentFile="${SECRETS_ENV}"' in script
    assert "--property=NoNewPrivileges=yes" in script
    assert '"${APP_DIR}/.venv/bin/alembic" upgrade head' in script
    assert "PALWORLD_REST_PASSWORD=" not in script
    assert "DISCORD_WEBHOOK_URL=" not in script


def test_web_and_worker_are_restarted_and_validated_separately() -> None:
    script = _script()

    assert '/usr/bin/systemctl restart "${WEB_SERVICE}"' in script
    assert '/usr/bin/systemctl restart "${WORKER_SERVICE}"' in script
    assert "http://127.0.0.1:8080/health" in script
    assert "worker_heartbeats" in script
    assert "julianday(started_at)" in script
    assert "julianday(heartbeat_at)" in script
    assert "o worker não passou na validação systemd + heartbeat" in script
    assert "http://127.0.0.1:8080/health" not in script.split("validate_worker() {", maxsplit=1)[1]


def test_pipeline_orders_activation_and_never_auto_rolls_back() -> None:
    script = _script()
    main = script.split("main() {", maxsplit=1)[1]

    ordered_steps = (
        "select_target_commit",
        "validate_migration_compatibility",
        "prepare_candidate",
        "check_candidate",
        "stop_services",
        "activate_candidate",
        "validate_runtime_configuration",
        "run_migrations",
        "restart_services",
        "validate_web",
        "validate_worker",
    )
    positions = [main.index(step) for step in ordered_steps]
    assert positions == sorted(positions)
    activation = script.split("activate_candidate() {", maxsplit=1)[1].split(
        "install_operational_files() {", maxsplit=1
    )[0]
    assert activation.index("record_previous_commit") < activation.index(
        'checkout --detach "${TARGET_COMMIT}"'
    )
    assert "rollback " not in cleanup_function(script).casefold()


def cleanup_function(script: str) -> str:
    return script.split("cleanup() {", maxsplit=1)[1].split("parse_arguments() {", maxsplit=1)[0]
