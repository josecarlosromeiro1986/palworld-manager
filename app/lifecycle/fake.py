from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import AppSetting
from app.health.palworld import PalworldHealthSnapshot, PalworldHealthState
from app.integrations.palworld_rest import RestApiState
from app.system.palworld_service import PalworldServiceStatus, PalworldSignal

FAKE_PALWORLD_ACTIVE_KEY = "development_fake_palworld_active"


class PersistentFakePalworldEnvironment:
    """Fake compartilhado pela web e pelo worker somente fora de production."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._restart_pending = False

    def _is_active(self) -> bool:
        with session_scope(self._session_factory) as session:
            setting = session.get(AppSetting, FAKE_PALWORLD_ACTIVE_KEY)
            return setting is not None and setting.value is True

    def _set_active(self, active: bool) -> None:
        with session_scope(self._session_factory) as session:
            setting = session.get(AppSetting, FAKE_PALWORLD_ACTIVE_KEY)
            if setting is None:
                session.add(AppSetting(key=FAKE_PALWORLD_ACTIVE_KEY, value=active))
            else:
                setting.value = active

    def get_status(self) -> PalworldServiceStatus:
        active = self._is_active()
        return PalworldServiceStatus(
            active=active,
            source_state="active" if active else "inactive",
        )

    def start(self) -> None:
        self._set_active(True)
        self._restart_pending = False

    def stop(self) -> None:
        self._set_active(False)
        self._restart_pending = False

    def restart(self) -> None:
        self._set_active(True)
        self._restart_pending = True

    def send_signal(self, signal: PalworldSignal) -> None:
        del signal
        self._set_active(False)

    def is_open(self) -> bool:
        return self._is_active()

    def check(self) -> PalworldHealthSnapshot:
        if self._restart_pending:
            self._restart_pending = False
            return PalworldHealthSnapshot(
                state=PalworldHealthState.STARTING,
                service_state="activating",
                process_running=True,
                rest_api_state=RestApiState.UNAVAILABLE,
            )
        active = self._is_active()
        return PalworldHealthSnapshot(
            state=PalworldHealthState.ONLINE if active else PalworldHealthState.OFFLINE,
            service_state="active" if active else "inactive",
            process_running=active,
            rest_api_state=(RestApiState.AVAILABLE if active else RestApiState.UNAVAILABLE),
        )
