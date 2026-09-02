from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.updates.service import (
    PALWORLD_APP_ID,
    CommandResult,
    FakeSteamCmdGateway,
    ProductionSteamCmdGateway,
    SteamCmdError,
    parse_keyvalues,
    parse_public_build,
)


def _app_info(build_id: str = "12345678", timestamp: str = "1786708800") -> str:
    return f'''AppID : {PALWORLD_APP_ID}, change number : 1/1,
"{PALWORLD_APP_ID}"
{{
    "depots"
    {{
        "branches"
        {{
            "public"
            {{
                "buildid" "{build_id}"
                "timeupdated" "{timestamp}"
            }}
        }}
    }}
}}
'''


def test_keyvalues_and_public_build_parser_use_official_public_branch() -> None:
    parsed = parse_keyvalues(_app_info())
    build_id, published_at = parse_public_build(_app_info())

    assert PALWORLD_APP_ID in parsed
    assert build_id == "12345678"
    assert published_at == datetime.fromtimestamp(1786708800, tz=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        '"2394010" { "depots" { } }',
        '"2394010" { "depots" { "branches" { "public" { "buildid" "bad" } } } }',
        '"2394010" {',
    ],
)
def test_public_build_parser_rejects_missing_invalid_or_incomplete_data(payload: str) -> None:
    with pytest.raises(SteamCmdError):
        parse_public_build(payload)


def test_invalid_timestamp_is_omitted_instead_of_inferred() -> None:
    build_id, published_at = parse_public_build(_app_info(timestamp="not-a-date"))

    assert build_id == "12345678"
    assert published_at is None


def test_production_gateway_uses_fixed_arguments_and_validates_update_confirmation(
    tmp_path: Path,
) -> None:
    steamcmd = tmp_path / "steamcmd"
    steamcmd.write_bytes(b"fake executable")
    steamcmd.chmod(0o700)
    install = tmp_path / "palserver"
    manifest = install / "steamapps" / f"appmanifest_{PALWORLD_APP_ID}.acf"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f'"AppState" {{ "appid" "{PALWORLD_APP_ID}" "buildid" "12345677" }}',
        encoding="utf-8",
    )
    commands: list[tuple[str, ...]] = []

    def runner(command: Sequence[str], *, timeout_seconds: int) -> CommandResult:
        assert timeout_seconds > 0
        commands.append(tuple(command))
        if "+app_info_print" in command:
            return CommandResult(0, _app_info().encode())
        return CommandResult(
            0,
            f"Success! App '{PALWORLD_APP_ID}' fully installed.\n".encode(),
        )

    gateway = ProductionSteamCmdGateway(steamcmd, install, runner=runner)
    checked = gateway.check()
    gateway.apply_update()

    assert checked.installed_build_id == "12345677"
    assert checked.available_build_id == "12345678"
    assert commands[0][1:] == (
        "+login",
        "anonymous",
        "+app_info_print",
        PALWORLD_APP_ID,
        "+quit",
    )
    assert commands[1][1:] == (
        "+force_install_dir",
        str(install.resolve()),
        "+login",
        "anonymous",
        "+app_update",
        PALWORLD_APP_ID,
        "validate",
        "+quit",
    )


def test_production_gateway_rejects_truncated_or_unconfirmed_output(tmp_path: Path) -> None:
    steamcmd = tmp_path / "steamcmd"
    steamcmd.write_bytes(b"fake executable")
    steamcmd.chmod(0o700)
    install = tmp_path / "palserver"
    (install / "steamapps").mkdir(parents=True)

    def runner(command: Sequence[str], *, timeout_seconds: int) -> CommandResult:
        del command, timeout_seconds
        return CommandResult(0, b"unconfirmed", output_truncated=True)

    gateway = ProductionSteamCmdGateway(steamcmd, install, runner=runner)
    with pytest.raises(SteamCmdError):
        gateway.apply_update()


def test_production_gateway_rejects_relative_structural_paths(tmp_path: Path) -> None:
    install = tmp_path / "palserver"
    install.mkdir()

    with pytest.raises(SteamCmdError, match="absoluto"):
        ProductionSteamCmdGateway(Path("steamcmd"), install)


def test_fake_is_integral_and_updates_without_external_processes() -> None:
    fake = FakeSteamCmdGateway(installed_build_id="1", available_build_id="2")

    assert fake.check().update_available is True
    fake.apply_update()

    assert fake.update_calls == 1
    assert fake.check().installed_build_id == "2"
    assert fake.check().update_available is False
