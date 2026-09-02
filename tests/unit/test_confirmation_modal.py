from pathlib import Path


def test_frontend_uses_shared_confirmation_modal_instead_of_native_dialogs() -> None:
    templates = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("app/templates").rglob("*.html")
    )
    script = Path("app/static/src/app.js").read_text(encoding="utf-8")
    layout = Path("app/templates/layouts/app.html").read_text(encoding="utf-8")

    assert 'include "components/confirmation_modal.html"' in layout
    assert "hx-confirm" not in templates
    assert "window.alert" not in script
    assert "window.confirm" not in script
    assert "window.prompt" not in script
    assert 'form.hasAttribute("data-confirm")' in script
    assert 'document.addEventListener(\n    "submit"' in script
    assert "new FormData(form)" in script
    assert 'document.querySelectorAll("form[data-confirm-key]")' in script
    assert "restoreFormValues(form, formValues)" in script


def test_action_panels_swap_expected_validation_errors() -> None:
    script = Path("app/static/src/app.js").read_text(encoding="utf-8")

    panel_config_start = script.index("const validationErrorPanelIds")
    panel_config_end = script.index("]);", panel_config_start)
    panel_config = script[panel_config_start:panel_config_end]
    for panel_id in (
        "restore-job",
        "drive-job",
        "update-operation",
        "host-power-feedback",
    ):
        assert f'"{panel_id}"' in panel_config
    assert "validationErrorPanelIds.has(target?.id)" in script
    assert "status === 400 || status === 409" in script
    assert "event.detail.shouldSwap = true" in script
    assert "event.detail.isError = false" in script
