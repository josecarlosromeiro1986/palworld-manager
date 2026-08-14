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
