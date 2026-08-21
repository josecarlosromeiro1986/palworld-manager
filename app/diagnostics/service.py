from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.backups.drive_jobs import DRIVE_CHECK_JOB_KIND
from app.config import AppEnvironment, Settings
from app.dashboard.metrics import HostMetricsService
from app.db.models import Job, NotificationEvent
from app.diagnostics.models import DiagnosticCheck, DiagnosticReport, DiagnosticStatus
from app.diagnostics.probes import (
    EnvironmentDiagnosticsProbe,
    create_environment_diagnostics_probe,
)
from app.health.palworld import PalworldHealthChecker, PalworldHealthState
from app.integrations.palworld_rest import RestApiState
from app.jobs.health import WorkerHealthChecker, WorkerHealthState
from app.logs.service import LogCategory, PalworldLogError, PalworldLogSource
from app.manager_settings.service import configured_disk_thresholds
from app.notifications.service import (
    DISCORD_TEST,
    NOTIFICATION_CHANNEL_DISCORD,
    NOTIFICATION_STATUS_FAILED,
    NOTIFICATION_STATUS_PENDING,
    NOTIFICATION_STATUS_SENDING,
    NOTIFICATION_STATUS_SENT,
)
from app.updates.jobs import UPDATE_CHECK_JOB_KIND


class DiagnosticsService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        palworld_health: PalworldHealthChecker,
        worker_health: WorkerHealthChecker,
        metrics: HostMetricsService,
        palworld_logs: PalworldLogSource,
        environment: EnvironmentDiagnosticsProbe,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._palworld_health = palworld_health
        self._worker_health = worker_health
        self._metrics = metrics
        self._palworld_logs = palworld_logs
        self._environment = environment
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self) -> DiagnosticReport:
        generated_at = self._clock()
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("o relógio do diagnóstico deve retornar timezone")
        generated_at = generated_at.astimezone(UTC)
        checks = (
            *self._environment.checks(),
            self._palworld_check(),
            self._worker_check(),
            self._resources_check(),
            self._steamcmd_check(),
            self._drive_check(),
            self._discord_check(),
            self._database_check(),
            self._relevant_errors_check(generated_at),
        )
        return DiagnosticReport(generated_at=generated_at, checks=checks)

    def _palworld_check(self) -> DiagnosticCheck:
        try:
            health = self._palworld_health.check()
        except Exception:
            return DiagnosticCheck(
                "palworld-health",
                "Palworld e worker",
                "Serviço, processo e REST do Palworld",
                DiagnosticStatus.FAILURE,
                "O health check do Palworld não pôde ser concluído.",
            )
        status = {
            PalworldHealthState.ONLINE: DiagnosticStatus.OK,
            PalworldHealthState.STARTING: DiagnosticStatus.ATTENTION,
            PalworldHealthState.DEGRADED: DiagnosticStatus.ATTENTION,
            PalworldHealthState.OFFLINE: DiagnosticStatus.ATTENTION,
            PalworldHealthState.FAILURE: DiagnosticStatus.FAILURE,
        }[health.state]
        process = (
            "ativo"
            if health.process_running is True
            else "inativo"
            if health.process_running is False
            else "indisponível"
        )
        rest = {
            RestApiState.AVAILABLE: "disponível",
            RestApiState.UNAUTHORIZED: "não autorizada",
            RestApiState.UNAVAILABLE: "indisponível",
            RestApiState.INVALID_RESPONSE: "resposta inválida",
            RestApiState.FAILURE: "falha",
        }[health.rest_api_state]
        service = health.service_state or "indisponível"
        return DiagnosticCheck(
            "palworld-health",
            "Palworld e worker",
            "Serviço, processo e REST do Palworld",
            status,
            f"Health {health.state.value}; systemd {service}; processo {process}; REST {rest}.",
        )

    def _worker_check(self) -> DiagnosticCheck:
        try:
            health = self._worker_health.check()
        except Exception:
            return DiagnosticCheck(
                "worker-health",
                "Palworld e worker",
                "Serviço e heartbeat do worker",
                DiagnosticStatus.FAILURE,
                "O health check do worker não pôde ser concluído.",
            )
        status = {
            WorkerHealthState.HEALTHY: DiagnosticStatus.OK,
            WorkerHealthState.STARTING: DiagnosticStatus.ATTENTION,
            WorkerHealthState.OFFLINE: DiagnosticStatus.ATTENTION,
            WorkerHealthState.UNRESPONSIVE: DiagnosticStatus.FAILURE,
        }[health.state]
        heartbeat = (
            f"heartbeat há {health.heartbeat_age_seconds:.1f} s"
            if health.heartbeat_age_seconds is not None
            else "sem heartbeat válido"
        )
        return DiagnosticCheck(
            "worker-health",
            "Palworld e worker",
            "Serviço e heartbeat do worker",
            status,
            f"Health {health.state.value}; systemd {health.service_state}; {heartbeat}.",
        )

    def _resources_check(self) -> DiagnosticCheck:
        try:
            current = self._metrics.collect().current
            with self._session_factory() as session:
                warning_gb, critical_gb = configured_disk_thresholds(session)
        except Exception:
            return DiagnosticCheck(
                "host-resources",
                "Manager e host",
                "Disco e memória",
                DiagnosticStatus.FAILURE,
                "Não foi possível consultar os recursos do host.",
            )
        disk_free_gb = current.disk_free_bytes / 1024**3
        if disk_free_gb < critical_gb:
            status = DiagnosticStatus.FAILURE
        elif disk_free_gb < warning_gb:
            status = DiagnosticStatus.ATTENTION
        else:
            status = DiagnosticStatus.OK
        return DiagnosticCheck(
            "host-resources",
            "Manager e host",
            "Disco e memória",
            status,
            f"RAM {current.memory_percent:.1f}% em uso; disco com {disk_free_gb:.1f} GiB livres.",
        )

    def _drive_check(self) -> DiagnosticCheck:
        try:
            with self._session_factory() as session:
                job = session.scalar(
                    select(Job)
                    .where(Job.kind == DRIVE_CHECK_JOB_KIND)
                    .order_by(Job.id.desc())
                    .limit(1)
                )
        except SQLAlchemyError:
            return DiagnosticCheck(
                "drive",
                "Integrações e conectividade",
                "rclone e Google Drive",
                DiagnosticStatus.FAILURE,
                "O último teste seguro do Drive não pôde ser consultado.",
            )
        if job is None:
            status = (
                DiagnosticStatus.OK
                if self._settings.environment is not AppEnvironment.PRODUCTION
                else DiagnosticStatus.ATTENTION
            )
            summary = (
                "Adapter fake disponível; nenhum teste persistido pelo worker."
                if status is DiagnosticStatus.OK
                else "Ainda não existe teste de conexão executado pelo worker."
            )
        elif job.status == "SUCCEEDED":
            result = job.result or {}
            remote_count = _safe_non_negative_int(result.get("remote_count"))
            summary = "Último teste do worker concluído com sucesso"
            if remote_count is not None:
                summary += f"; {remote_count} objeto(s) gerenciado(s)"
            summary += "."
            status = DiagnosticStatus.OK
        elif job.status in {"FAILED", "INTERRUPTED"}:
            status = DiagnosticStatus.FAILURE
            summary = "O último teste do worker falhou de forma controlada."
        else:
            status = DiagnosticStatus.ATTENTION
            summary = "Existe um teste do Drive aguardando ou em execução pelo worker."
        return DiagnosticCheck(
            "drive",
            "Integrações e conectividade",
            "rclone e Google Drive",
            status,
            summary,
        )

    def _steamcmd_check(self) -> DiagnosticCheck:
        try:
            with self._session_factory() as session:
                job = session.scalar(
                    select(Job)
                    .where(Job.kind == UPDATE_CHECK_JOB_KIND)
                    .order_by(Job.id.desc())
                    .limit(1)
                )
        except SQLAlchemyError:
            return DiagnosticCheck(
                "steamcmd",
                "Integrações e conectividade",
                "SteamCMD e conectividade Steam",
                DiagnosticStatus.FAILURE,
                "O último check seguro do SteamCMD não pôde ser consultado.",
            )
        if job is None:
            status = (
                DiagnosticStatus.OK
                if self._settings.environment is not AppEnvironment.PRODUCTION
                else DiagnosticStatus.ATTENTION
            )
            summary = (
                "Adapter fake disponível; nenhum check persistido pelo worker."
                if status is DiagnosticStatus.OK
                else "Ainda não existe check de versão executado pelo worker."
            )
        elif job.status == "SUCCEEDED":
            result = job.result or {}
            installed = _safe_build_id(result.get("installed_build_id"))
            available = _safe_build_id(result.get("available_build_id"))
            if installed is None or available is None:
                status = DiagnosticStatus.FAILURE
                summary = "O último check concluiu com resultado inválido."
            else:
                status = DiagnosticStatus.OK
                update = (
                    "atualização disponível" if installed != available else "instalação atualizada"
                )
                summary = (
                    f"Último check do worker: build instalado {installed}; "
                    f"build público {available}; {update}."
                )
        elif job.status in {"FAILED", "INTERRUPTED"}:
            status = DiagnosticStatus.FAILURE
            summary = "O último check do worker falhou de forma controlada."
        else:
            status = DiagnosticStatus.ATTENTION
            summary = "Existe um check de versão aguardando ou em execução pelo worker."
        return DiagnosticCheck(
            "steamcmd",
            "Integrações e conectividade",
            "SteamCMD e conectividade Steam",
            status,
            summary,
        )

    def _discord_check(self) -> DiagnosticCheck:
        try:
            with self._session_factory() as session:
                event = session.scalar(
                    select(NotificationEvent)
                    .where(
                        NotificationEvent.event_type == DISCORD_TEST,
                        NotificationEvent.channel == NOTIFICATION_CHANNEL_DISCORD,
                    )
                    .order_by(NotificationEvent.id.desc())
                    .limit(1)
                )
        except SQLAlchemyError:
            event = None
            query_failed = True
        else:
            query_failed = False
        configured = (
            self._settings.environment is not AppEnvironment.PRODUCTION
            or self._settings.discord_webhook_url is not None
        )
        if query_failed:
            status = DiagnosticStatus.FAILURE
            summary = "O estado seguro do teste Discord não pôde ser consultado."
        elif not configured:
            status = DiagnosticStatus.ATTENTION
            summary = "Webhook não configurado; nenhum valor sensível foi exibido."
        elif event is None:
            status = (
                DiagnosticStatus.OK
                if self._settings.environment is not AppEnvironment.PRODUCTION
                else DiagnosticStatus.ATTENTION
            )
            summary = (
                "Adapter fake disponível; nenhum teste persistido pelo worker."
                if status is DiagnosticStatus.OK
                else "Webhook configurado, mas ainda não houve teste entregue pelo worker."
            )
        elif event.status == NOTIFICATION_STATUS_SENT:
            status = DiagnosticStatus.OK
            summary = "O último teste foi entregue pelo worker."
        elif event.status == NOTIFICATION_STATUS_FAILED:
            status = DiagnosticStatus.FAILURE
            summary = "O último teste terminou em falha controlada."
        elif event.status in {NOTIFICATION_STATUS_PENDING, NOTIFICATION_STATUS_SENDING}:
            status = DiagnosticStatus.ATTENTION
            summary = "O teste mais recente aguarda entrega ou confirmação do worker."
        else:
            status = DiagnosticStatus.FAILURE
            summary = "O teste mais recente possui estado inválido."
        return DiagnosticCheck(
            "discord",
            "Integrações e conectividade",
            "Discord",
            status,
            summary,
        )

    def _database_check(self) -> DiagnosticCheck:
        try:
            with self._session_factory() as session:
                integrity = session.execute(text("PRAGMA integrity_check")).scalar_one()
                current_revision = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            configuration = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            expected_revision = ScriptDirectory.from_config(configuration).get_current_head()
        except (OSError, SQLAlchemyError, ValueError):
            return DiagnosticCheck(
                "database",
                "Dados e erros",
                "SQLite e migrations",
                DiagnosticStatus.FAILURE,
                "Integridade ou revisão do banco não puderam ser confirmadas.",
            )
        if integrity == "ok" and current_revision == expected_revision:
            status = DiagnosticStatus.OK
            summary = f"Integridade confirmada; migration {current_revision}."
        else:
            status = DiagnosticStatus.FAILURE
            summary = "O banco está íntegro apenas quando integrity_check e migration coincidem."
        return DiagnosticCheck(
            "database",
            "Dados e erros",
            "SQLite e migrations",
            status,
            summary,
        )

    def _relevant_errors_check(self, generated_at: datetime) -> DiagnosticCheck:
        log_errors: int | None
        log_warnings: int | None
        try:
            entries = self._palworld_logs.history(100)
            log_errors = sum(entry.category is LogCategory.ERROR for entry in entries)
            log_warnings = sum(entry.category is LogCategory.WARNING for entry in entries)
        except (OSError, PalworldLogError, ValueError):
            log_errors = None
            log_warnings = None
        try:
            with self._session_factory() as session:
                failed_jobs = session.scalar(
                    select(func.count(Job.id)).where(
                        Job.status.in_(("FAILED", "INTERRUPTED")),
                        Job.created_at >= generated_at - timedelta(hours=24),
                    )
                )
        except SQLAlchemyError:
            failed_jobs = None
        if log_errors is None or log_warnings is None or failed_jobs is None:
            status = DiagnosticStatus.ATTENTION
            summary = "Uma ou mais fontes de erros recentes não puderam ser consultadas."
        elif log_errors or failed_jobs:
            status = DiagnosticStatus.ATTENTION
            summary = (
                f"Últimas 100 linhas: {log_errors} erro(s), {log_warnings} aviso(s); "
                f"jobs falhos/interrompidos em 24 h: {failed_jobs}."
            )
        elif log_warnings:
            status = DiagnosticStatus.ATTENTION
            summary = (
                f"Últimas 100 linhas: nenhum erro e {log_warnings} aviso(s); "
                "nenhum job falho/interrompido em 24 h."
            )
        else:
            status = DiagnosticStatus.OK
            summary = "Nenhum erro recente nos logs consultados ou nos jobs das últimas 24 h."
        return DiagnosticCheck(
            "relevant-errors",
            "Dados e erros",
            "Erros relevantes",
            status,
            summary,
        )


def create_diagnostics_service(
    settings: Settings,
    session_factory: sessionmaker[Session],
    palworld_health: PalworldHealthChecker,
    worker_health: WorkerHealthChecker,
    metrics: HostMetricsService,
    palworld_logs: PalworldLogSource,
) -> DiagnosticsService:
    return DiagnosticsService(
        settings,
        session_factory,
        palworld_health,
        worker_health,
        metrics,
        palworld_logs,
        create_environment_diagnostics_probe(settings),
    )


def _safe_non_negative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_build_id(value: object) -> str | None:
    if isinstance(value, str) and value.isdecimal() and 1 <= len(value) <= 20:
        return value
    return None
