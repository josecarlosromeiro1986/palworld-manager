from datetime import date

import pytest

from app.audit.history import AuditFilterError, AuditFilters, parse_audit_filters
from app.audit.service import PROTECTED_VALUE, redact_audit_details, redact_audit_text


def _parse(**overrides: str | None) -> AuditFilters:
    values: dict[str, str | None] = {
        "date_from": None,
        "date_to": None,
        "action": None,
        "result": None,
        "origin": None,
        "user_id": None,
        "target": None,
        "page": None,
    }
    values.update(overrides)
    return parse_audit_filters(
        date_from=values["date_from"],
        date_to=values["date_to"],
        action=values["action"],
        result=values["result"],
        origin=values["origin"],
        user_id=values["user_id"],
        target=values["target"],
        page=values["page"],
    )


def test_audit_filters_parse_all_supported_fields_and_preserve_pagination() -> None:
    filters = _parse(
        date_from="2026-08-01",
        date_to="2026-08-21",
        action="BACKUP",
        result="SUCCESS",
        origin="AUTOMATIC",
        user_id="7",
        target="Backup local",
        page="3",
    )

    assert filters.date_from == date(2026, 8, 1)
    assert filters.date_to == date(2026, 8, 21)
    assert filters.user_id == 7
    assert filters.page == 3
    assert ("origin", "AUTOMATIC") in filters.query_parameters(page=2)
    assert ("page", "2") in filters.query_parameters(page=2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("date_from", "21/08/2026"),
        ("action", "DROP TABLE"),
        ("result", "UNKNOWN"),
        ("origin", "UNKNOWN"),
        ("user_id", "0"),
        ("page", "-1"),
        ("target", "x" * 256),
    ],
)
def test_audit_filters_reject_invalid_values(field: str, value: str) -> None:
    with pytest.raises(AuditFilterError):
        _parse(**{field: value})


def test_audit_filters_reject_reversed_period() -> None:
    with pytest.raises(AuditFilterError):
        _parse(date_from="2026-08-21", date_to="2026-08-20")


def test_audit_redacts_api_key_assignments_and_detail_keys() -> None:
    assert redact_audit_text("api_key=valor-protegido") == f"api_key={PROTECTED_VALUE}"
    assert redact_audit_details({"api-key": "valor-protegido"}) == {"api-key": PROTECTED_VALUE}
