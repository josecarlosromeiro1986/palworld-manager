import subprocess
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
OPS_ROOT = PROJECT_ROOT / "ops"

WEB_UNIT = OPS_ROOT / "systemd/palworld-manager.service"
WORKER_UNIT = OPS_ROOT / "systemd/palworld-manager-worker.service"
HOST_CONTROL_UNIT = OPS_ROOT / "systemd/palworld-manager-host-control@.service"
HOST_CONTROL_PATH_UNIT = OPS_ROOT / "systemd/palworld-manager-host-control@.path"
HOST_CONTROL_SCRIPT = OPS_ROOT / "scripts/palworld-manager-host-control"
PALWORLD_DROP_IN = OPS_ROOT / "systemd/palworld.service.d/10-palworld-manager-access.conf"
MANAGER_ENV = OPS_ROOT / "environment/manager.env"
TMPFILES = OPS_ROOT / "tmpfiles/palworld-manager.conf"
PRODUCTION_INSTALL = PROJECT_ROOT / "docs/operations/production-install.md"

EXPECTED_ROOT_COMMANDS = {
    "/usr/bin/systemctl --no-block start palworld.service",
    "/usr/bin/systemctl --no-block stop palworld.service",
    "/usr/bin/systemctl --no-block restart palworld.service",
    "/usr/bin/systemctl kill --kill-whom=main --signal=SIGTERM palworld.service",
    "/usr/bin/systemctl kill --kill-whom=main --signal=SIGKILL palworld.service",
    "/usr/bin/systemctl --no-block reboot",
    "/usr/bin/systemctl --no-block poweroff",
}
SECRET_NAMES = {
    "DISCORD_WEBHOOK_URL",
    "PALWORLD_REST_PASSWORD",
    "PALWORLD_REST_USERNAME",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in _read(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None, f"diretiva fora de seção em {path}: {line}"
        key, separator, value = line.partition("=")
        assert separator, f"diretiva inválida em {path}: {line}"
        current.setdefault(key, []).append(value)
    return sections


def _single(service: dict[str, list[str]], key: str) -> str:
    values = service[key]
    assert len(values) == 1
    return values[0]


def test_systemd_units_keep_web_and_worker_non_root_and_independent() -> None:
    web = _parse_unit(WEB_UNIT)["Service"]
    worker = _parse_unit(WORKER_UNIT)["Service"]

    for service in (web, worker):
        assert _single(service, "User") == "palmanager"
        assert _single(service, "Group") == "palmanager"
        assert _single(service, "WorkingDirectory") == "/opt/palworld-manager"
        assert _single(service, "UMask") == "0027"
        assert _single(service, "StandardOutput") == "journal"
        assert _single(service, "StandardError") == "journal"
        assert service["EnvironmentFile"] == [
            "/etc/palworld-manager/manager.env",
            "/etc/palworld-manager/secrets.env",
        ]
        assert "palworld-manager" in _single(service, "SupplementaryGroups").split()
        assert "systemd-journal" in _single(service, "SupplementaryGroups").split()

    assert _single(web, "ExecStart").endswith(" -m app.web")
    assert _single(worker, "ExecStart").endswith(" -m app.worker")
    assert "app.worker" not in _single(web, "ExecStart")
    assert "app.web" not in _single(worker, "ExecStart")
    assert _single(web, "NoNewPrivileges") == "true"
    assert _single(worker, "NoNewPrivileges") == "true"
    assert _single(worker, "RestrictSUIDSGID") == "true"
    assert web["ReadWritePaths"] == [
        "/var/lib/palworld-manager",
        "/home/steam/palserver/Pal/Saved/Config/LinuxServer",
    ]
    assert worker["ReadWritePaths"] == [
        "/var/lib/palworld-manager",
        "/home/steam/palserver",
        "/run/palworld-manager/host-control",
    ]


def test_palworld_drop_in_preserves_shared_group_for_new_files() -> None:
    service = _parse_unit(PALWORLD_DROP_IN)["Service"]

    assert service == {
        "SupplementaryGroups": ["palworld-manager"],
        "UMask": ["0007"],
    }


def test_production_environment_binds_web_to_loopback_without_secrets() -> None:
    entries = dict(
        line.split("=", maxsplit=1)
        for line in _read(MANAGER_ENV).splitlines()
        if line and not line.startswith("#")
    )

    assert entries["APP_ENVIRONMENT"] == "production"
    assert entries["APP_HOST"] == "127.0.0.1"
    assert entries["APP_PORT"] == "8080"
    assert entries["MANAGER_DATABASE"] == "/var/lib/palworld-manager/manager.db"
    assert entries["RCLONE_CONFIG"] == ("/var/lib/palworld-manager/rclone/rclone.conf")
    assert SECRET_NAMES.isdisjoint(entries)


def test_host_control_is_a_hardened_root_oneshot() -> None:
    unit = _parse_unit(HOST_CONTROL_UNIT)
    service = unit["Service"]

    assert _single(unit["Unit"], "ConditionPathExists") == (
        "/run/palworld-manager/host-control/%i.request"
    )
    assert _single(service, "Type") == "oneshot"
    assert _single(service, "User") == "root"
    assert _single(service, "Group") == "root"
    assert _single(service, "ExecStart") == ("/usr/local/sbin/palworld-manager-host-control %i")
    assert _single(service, "NoNewPrivileges") == "true"
    assert _single(service, "RestrictSUIDSGID") == "true"
    assert _single(service, "CapabilityBoundingSet") == ""
    assert _single(service, "ReadWritePaths") == "/run/palworld-manager/host-control"
    assert "Install" not in _parse_unit(HOST_CONTROL_UNIT)


def test_host_control_path_template_maps_only_exact_request_names() -> None:
    unit = _parse_unit(HOST_CONTROL_PATH_UNIT)

    assert unit["Path"] == {
        "PathExists": ["/run/palworld-manager/host-control/%i.request"],
        "Unit": ["palworld-manager-host-control@%i.service"],
    }
    assert unit["Install"] == {"WantedBy": ["multi-user.target"]}


def test_host_control_helper_has_valid_bash_syntax() -> None:
    bash_check = subprocess.run(
        ["bash", "-n", str(HOST_CONTROL_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert bash_check.returncode == 0, bash_check.stderr


def test_host_control_helper_executes_only_the_seven_fixed_root_commands() -> None:
    content = _read(HOST_CONTROL_SCRIPT)
    commands = {
        line.removeprefix("command=(").removesuffix(")")
        for raw_line in content.splitlines()
        if (line := raw_line.strip()).startswith("command=(")
    }

    assert commands == EXPECTED_ROOT_COMMANDS
    assert "eval " not in content
    assert "sudo" not in content.casefold()
    assert content.count("exit 64") == 2
    assert content.count("exit 66") == 2
    assert "palmanager:palmanager:600:0" in content
    assert content.index('/bin/rm -- "$request_file"') < content.index('exec "${command[@]}"')


def test_privileged_transport_has_no_polkit_or_sudoers_grant() -> None:
    assert not (OPS_ROOT / "polkit/50-palworld-manager-host-control.rules").exists()
    assert not (OPS_ROOT / "sudoers/palworld-manager").exists()


def test_tmpfiles_uses_minimum_manager_directory_modes() -> None:
    entries = {
        parts[1]: tuple(parts[2:5])
        for line in _read(TMPFILES).splitlines()
        if line and not line.startswith("#")
        for parts in [line.split()]
    }

    assert entries["/etc/palworld-manager"] == ("0750", "root", "palmanager")
    assert entries["/run/palworld-manager"] == ("0750", "root", "palmanager")
    assert entries["/run/palworld-manager/host-control"] == (
        "0770",
        "root",
        "palmanager",
    )
    assert entries["/var/lib/palworld-manager"] == (
        "0750",
        "palmanager",
        "palmanager",
    )
    assert entries["/var/lib/palworld-manager/backups"] == (
        "0750",
        "palmanager",
        "palmanager",
    )
    assert entries["/var/lib/palworld-manager/jobs"] == (
        "0750",
        "palmanager",
        "palmanager",
    )
    assert entries["/var/lib/palworld-manager/rclone"] == (
        "0700",
        "palmanager",
        "palmanager",
    )
    assert all(mode in {"0700", "0750", "0770"} for mode, _owner, _group in entries.values())


def test_initial_transient_services_preserve_manager_file_modes() -> None:
    content = _read(PRODUCTION_INSTALL)

    assert content.count("--property=UMask=0027") == 2
    assert "'palmanager:palmanager:640'" in content
    assert "sqlite3 -readonly /var/lib/palworld-manager/manager.db" in content


def test_production_install_creates_venv_with_explicit_python_312() -> None:
    content = _read(PRODUCTION_INSTALL)

    assert "/usr/bin/python3.12 --version" in content
    assert "sudo -u palmanager /usr/bin/python3.12 -m venv /opt/palworld-manager/.venv" in content
    assert "sudo -u palmanager python3 -m venv" not in content


def test_python_package_includes_runtime_templates_and_built_assets() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    package_data = set(project["tool"]["setuptools"]["package-data"]["app"])
    assert "templates/**/*.html" in package_data
    assert "static/dist/*.css" in package_data
    assert "static/dist/*.js" in package_data
    assert "static/dist/*.svg" in package_data
    assert "static/dist/vendor/*.js" in package_data


def test_production_ops_has_no_standalone_rollback_wrapper() -> None:
    assert not any(path.name.casefold().startswith("rollback") for path in OPS_ROOT.rglob("*"))
