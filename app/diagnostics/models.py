from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DiagnosticStatus(StrEnum):
    OK = "OK"
    ATTENTION = "ATTENTION"
    FAILURE = "FAILURE"

    @property
    def symbol(self) -> str:
        return {
            DiagnosticStatus.OK: "✓",
            DiagnosticStatus.ATTENTION: "⚠",
            DiagnosticStatus.FAILURE: "✗",
        }[self]

    @property
    def label(self) -> str:
        return {
            DiagnosticStatus.OK: "OK",
            DiagnosticStatus.ATTENTION: "Atenção",
            DiagnosticStatus.FAILURE: "Falha",
        }[self]


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    identifier: str
    section: str
    label: str
    status: DiagnosticStatus
    summary: str


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    generated_at: datetime
    checks: tuple[DiagnosticCheck, ...]

    @property
    def overall_status(self) -> DiagnosticStatus:
        statuses = {check.status for check in self.checks}
        if DiagnosticStatus.FAILURE in statuses:
            return DiagnosticStatus.FAILURE
        if DiagnosticStatus.ATTENTION in statuses:
            return DiagnosticStatus.ATTENTION
        return DiagnosticStatus.OK

    @property
    def sections(self) -> tuple[tuple[str, tuple[DiagnosticCheck, ...]], ...]:
        names = tuple(dict.fromkeys(check.section for check in self.checks))
        return tuple(
            (name, tuple(check for check in self.checks if check.section == name)) for name in names
        )

    def copy_text(self) -> str:
        lines = [
            "Palworld Manager — Diagnóstico",
            f"Gerado em: {self.generated_at.isoformat()}",
            f"Resultado geral: {self.overall_status.symbol} {self.overall_status.label}",
        ]
        for section, checks in self.sections:
            lines.append("")
            lines.append(section)
            lines.extend(
                f"{check.status.symbol} {check.status.label} — {check.label}: {check.summary}"
                for check in checks
            )
        return "\n".join(lines)
