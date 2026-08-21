import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

from pydantic import SecretStr

from app.config import AppEnvironment, Settings
from app.diagnostics.models import DiagnosticCheck, DiagnosticReport, DiagnosticStatus
from app.diagnostics.probes import (
    SYSTEMCTL_PATH,
    TAILSCALE_PATH,
    FakeEnvironmentDiagnosticsProbe,
    ProductionEnvironmentDiagnosticsProbe,
)


def _production_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment=AppEnvironment.PRODUCTION,
        app_host=ip_address("127.0.0.1"),
        manager_database=tmp_path / "manager.db",
        palworld_dir=tmp_path / "palworld",
        palworld_settings=tmp_path / "palworld/PalWorldSettings.ini",
        steamcmd=tmp_path / "steamcmd",
        rclone=tmp_path / "rclone",
        palworld_rest_username=SecretStr("fake-user"),
        palworld_rest_password=SecretStr("fake-password"),
    )


def test_report_uses_worst_status_and_builds_copyable_text() -> None:
    report = DiagnosticReport(
        generated_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        checks=(
            DiagnosticCheck("one", "Manager", "Primeiro", DiagnosticStatus.OK, "Saudável."),
            DiagnosticCheck(
                "two", "Integrações", "Segundo", DiagnosticStatus.ATTENTION, "Revisar."
            ),
            DiagnosticCheck(
                "three", "Dados", "Terceiro", DiagnosticStatus.FAILURE, "Indisponível."
            ),
        ),
    )

    assert report.overall_status is DiagnosticStatus.FAILURE
    assert [section for section, _checks in report.sections] == [
        "Manager",
        "Integrações",
        "Dados",
    ]
    copied = report.copy_text()
    assert "Resultado geral: ✗ Falha" in copied
    assert "✓ OK — Primeiro: Saudável." in copied
    assert "⚠ Atenção — Segundo: Revisar." in copied
    assert "✗ Falha — Terceiro: Indisponível." in copied


def test_fake_probe_does_not_receive_or_expose_structural_configuration() -> None:
    checks = FakeEnvironmentDiagnosticsProbe(commit="7af506404a21").checks()
    rendered = " ".join(check.summary for check in checks)

    assert {check.identifier for check in checks} == {
        "manager-build",
        "web-service",
        "ports",
        "permissions",
        "tailscale",
    }
    assert "7af506404a21" in rendered
    assert "/home/steam" not in rendered
    assert "systemctl" not in rendered


def test_production_probe_uses_only_fixed_read_only_commands(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        assert timeout_seconds == 5.0
        normalized = tuple(command)
        calls.append(normalized)
        if normalized[:2] == (SYSTEMCTL_PATH, "show"):
            output = "ActiveState=active\nMainPID=123\n"
        elif normalized == (TAILSCALE_PATH, "status", "--json"):
            output = json.dumps({"BackendState": "Running"})
        elif normalized == (TAILSCALE_PATH, "serve", "status", "--json"):
            output = json.dumps({"TCP": {"443": {"HTTPS": True, "Proxy": "http://127.0.0.1:8080"}}})
        else:
            raise AssertionError(f"comando inesperado: {normalized!r}")
        return subprocess.CompletedProcess(normalized, 0, stdout=output, stderr="")

    probe = ProductionEnvironmentDiagnosticsProbe(
        _production_settings(tmp_path),
        commit="7af506404a21",
        runner=runner,
        process_inspector=lambda pid: pid == 123,
        port_checker=lambda _host, _port: True,
    )

    checks = {check.identifier: check for check in probe.checks()}

    assert checks["web-service"].status is DiagnosticStatus.OK
    assert checks["ports"].status is DiagnosticStatus.OK
    assert checks["tailscale"].status is DiagnosticStatus.OK
    assert calls == [
        (
            SYSTEMCTL_PATH,
            "show",
            "--property=ActiveState,MainPID",
            "palworld-manager.service",
        ),
        (TAILSCALE_PATH, "status", "--json"),
        (TAILSCALE_PATH, "serve", "status", "--json"),
    ]


def test_production_probe_never_copies_external_errors_or_secrets(tmp_path: Path) -> None:
    secret = "super-secret-token"

    def runner(
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        del timeout_seconds
        return subprocess.CompletedProcess(command, 1, stdout=secret, stderr=secret)

    checks = ProductionEnvironmentDiagnosticsProbe(
        _production_settings(tmp_path),
        commit="indisponível",
        runner=runner,
        process_inspector=lambda _pid: False,
        port_checker=lambda _host, _port: False,
    ).checks()
    rendered = " ".join(check.summary for check in checks)

    assert secret not in rendered
    assert "/home/steam" not in rendered
    assert "fake-password" not in rendered
    assert any(check.status is DiagnosticStatus.FAILURE for check in checks)
