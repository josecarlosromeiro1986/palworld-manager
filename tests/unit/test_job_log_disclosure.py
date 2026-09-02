from pathlib import Path


def test_job_log_disclosure_survives_htmx_polling_swaps() -> None:
    shutdown_template = Path("app/templates/dashboard/_shutdown_job.html").read_text(
        encoding="utf-8"
    )
    lifecycle_template = Path("app/templates/dashboard/_lifecycle_job.html").read_text(
        encoding="utf-8"
    )
    backup_template = Path("app/templates/backups/_job.html").read_text(encoding="utf-8")
    drive_template = Path("app/templates/backups/_drive_job.html").read_text(encoding="utf-8")
    script = Path("app/static/src/app.js").read_text(encoding="utf-8")

    assert 'data-job-log-key="shutdown-{{ job.id }}"' in shutdown_template
    assert 'data-job-log-key="lifecycle-{{ job.id }}"' in lifecycle_template
    assert 'data-job-log-key="backup-{{ job.id }}"' in backup_template
    assert 'data-job-log-key="drive-{{ drive_job.id }}"' in drive_template
    assert "rememberOpenJobLogs(event.detail.target)" in script
    assert 'document.querySelectorAll("details[data-job-log-key]")' in script
    assert "openJobLogKeys.add(key)" in script
    assert "openJobLogKeys.delete(key)" in script
    assert "details.open = true" in script
    assert 'document.body.addEventListener("htmx:beforeSwap"' in script
    assert 'document.body.addEventListener("htmx:afterSwap", restoreOpenJobLogs)' in script
