from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


def test_backup_job_filenames_wrap_inside_mobile_cards() -> None:
    templates = (
        PROJECT_ROOT / "app/templates/backups/_drive_job.html",
        PROJECT_ROOT / "app/templates/restores/_job.html",
    )

    for template in templates:
        content = template.read_text(encoding="utf-8")

        assert 'class="min-w-0 flex-1"' in content
        assert "break-all font-mono" in content

    drive_template = templates[0].read_text(encoding="utf-8")
    assert "truncate font-mono" not in drive_template
