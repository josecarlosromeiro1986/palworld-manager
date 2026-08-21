import pytest

from app.manager_settings.service import (
    DEFAULT_MANAGER_SETTINGS,
    ManagerSettingsValidationError,
    validate_manager_settings,
)


def _values(**overrides: object) -> dict[str, object]:
    return {**DEFAULT_MANAGER_SETTINGS, **overrides}


def test_manager_settings_defaults_match_the_v1_contract() -> None:
    values = validate_manager_settings(_values())

    assert values.timezone == "America/Sao_Paulo"
    assert values.backup_enabled is True
    assert values.backup_time == "04:00"
    assert values.local_backup_retention == 3
    assert values.drive_backup_retention == 10
    assert values.metrics_interval_seconds == 5
    assert values.assisted_shutdown_default_minutes == 5
    assert values.start_timeout_seconds == 120
    assert values.restart_timeout_seconds == 120
    assert values.stop_timeout_seconds == 60
    assert values.disk_warning_gb == 20
    assert values.disk_critical_gb == 10


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("local_backup_retention", 0),
        ("local_backup_retention", 31),
        ("drive_backup_retention", 0),
        ("drive_backup_retention", 101),
        ("metrics_interval_seconds", 0),
        ("metrics_interval_seconds", 61),
        ("disk_warning_gb", 0),
        ("disk_warning_gb", 1025),
        ("disk_critical_gb", 0),
        ("disk_critical_gb", 1025),
        ("start_timeout_seconds", 0),
        ("start_timeout_seconds", 601),
        ("restart_timeout_seconds", 0),
        ("restart_timeout_seconds", 601),
        ("stop_timeout_seconds", 0),
        ("stop_timeout_seconds", 301),
        ("assisted_shutdown_default_minutes", 2),
    ],
)
def test_rejects_values_outside_operational_limits(key: str, value: object) -> None:
    with pytest.raises(ManagerSettingsValidationError):
        validate_manager_settings(_values(**{key: value}))


@pytest.mark.parametrize("critical", [20, 21])
def test_disk_critical_must_be_strictly_lower_than_warning(critical: int) -> None:
    with pytest.raises(ManagerSettingsValidationError):
        validate_manager_settings(_values(disk_warning_gb=20, disk_critical_gb=critical))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("timezone", "Timezone/Inexistente"),
        ("backup_time", "4:00"),
        ("backup_time", "24:00"),
        ("backup_time", "04:00:00"),
        ("backup_enabled", 1),
        ("metrics_interval_seconds", True),
    ],
)
def test_rejects_invalid_types_timezone_and_time(key: str, value: object) -> None:
    with pytest.raises(ManagerSettingsValidationError):
        validate_manager_settings(_values(**{key: value}))


def test_accepts_all_approved_boundary_values() -> None:
    minimums = validate_manager_settings(
        _values(
            local_backup_retention=1,
            drive_backup_retention=1,
            metrics_interval_seconds=1,
            disk_warning_gb=2,
            disk_critical_gb=1,
            start_timeout_seconds=1,
            restart_timeout_seconds=1,
            stop_timeout_seconds=1,
        )
    )
    maximums = validate_manager_settings(
        _values(
            local_backup_retention=30,
            drive_backup_retention=100,
            metrics_interval_seconds=60,
            disk_warning_gb=1024,
            disk_critical_gb=1023,
            start_timeout_seconds=600,
            restart_timeout_seconds=600,
            stop_timeout_seconds=300,
        )
    )

    assert minimums.local_backup_retention == 1
    assert maximums.drive_backup_retention == 100
    assert maximums.disk_warning_gb == 1024
