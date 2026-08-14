from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.db.engine import session_scope
from app.palworld_settings.definitions import (
    PALWORLD_SETTINGS_SCHEMA_SOURCE,
    PALWORLD_SETTINGS_SCHEMA_VERSION,
    SETTING_CATEGORIES,
    SETTING_DEFINITIONS_BY_KEY,
    SettingDefinition,
    SettingKind,
)
from app.palworld_settings.ini import (
    IniEntry,
    IniParseError,
    ParsedIni,
    SettingValueError,
    parse_ini,
    parse_setting_value,
    serialize_setting_value,
)
from app.palworld_settings.storage import (
    PalworldSettingsStorage,
    PalworldSettingsStorageError,
    SettingsStorageErrorKind,
    StoredSettings,
)


class PalworldSettingsValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SettingFieldView:
    key: str
    label: str
    description: str
    kind: SettingKind
    value: str | None
    editable: bool
    issue: str | None
    options: tuple[str, ...]
    minimum: str | None
    maximum: str | None


@dataclass(frozen=True, slots=True)
class SettingCategoryView:
    name: str
    fields: tuple[SettingFieldView, ...]


@dataclass(frozen=True, slots=True)
class PalworldSettingsSnapshot:
    version: str
    categories: tuple[SettingCategoryView, ...]
    unknown_keys: tuple[str, ...]
    malformed_entries: int
    schema_version: str = PALWORLD_SETTINGS_SCHEMA_VERSION
    schema_source: str = PALWORLD_SETTINGS_SCHEMA_SOURCE

    @property
    def editable_keys(self) -> tuple[str, ...]:
        return tuple(
            field.key for category in self.categories for field in category.fields if field.editable
        )


@dataclass(frozen=True, slots=True)
class PalworldSettingsSaveResult:
    snapshot: PalworldSettingsSnapshot
    changed_fields: tuple[str, ...]
    backup_name: str | None


@dataclass(frozen=True, slots=True)
class _InspectedSettings:
    parsed: ParsedIni
    snapshot: PalworldSettingsSnapshot
    fields: dict[str, SettingFieldView]


def _setting_field_view(
    definition: SettingDefinition,
    *,
    value: str | None,
    editable: bool,
    issue: str | None,
) -> SettingFieldView:
    return SettingFieldView(
        key=definition.key,
        label=definition.label,
        description=definition.description,
        kind=definition.kind,
        value=value,
        editable=editable,
        issue=issue,
        options=definition.options,
        minimum=str(definition.minimum) if definition.minimum is not None else None,
        maximum=str(definition.maximum) if definition.maximum is not None else None,
    )


class PalworldSettingsService:
    def __init__(
        self,
        storage: PalworldSettingsStorage,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._storage = storage
        self._session_factory = session_factory
        self._clock = clock

    def load(self) -> PalworldSettingsSnapshot:
        stored = self._storage.read()
        return self._inspect(stored).snapshot

    def save(
        self,
        updates: Mapping[str, str],
        *,
        expected_version: str,
        administrator_user_id: int,
    ) -> PalworldSettingsSaveResult:
        safe_requested_keys = sorted(set(updates) & set(SETTING_DEFINITIONS_BY_KEY))
        try:
            stored = self._storage.read()
            if stored.version != expected_version:
                raise PalworldSettingsStorageError(SettingsStorageErrorKind.CONFLICT)
            inspected = self._inspect(stored)
            expected_keys = set(inspected.snapshot.editable_keys)
            received_keys = set(updates)
            if received_keys != expected_keys:
                raise PalworldSettingsValidationError(
                    "Os campos enviados não correspondem ao arquivo aberto. Recarregue a página."
                )
            replacements: dict[str, str] = {}
            changed_fields: list[str] = []
            for key in inspected.snapshot.editable_keys:
                field = inspected.fields[key]
                definition = SETTING_DEFINITIONS_BY_KEY[key]
                submitted = parse_setting_value(definition, updates[key])
                if submitted != field.value:
                    replacements[key] = serialize_setting_value(definition, updates[key])
                    changed_fields.append(key)

            if not replacements:
                self._audit(
                    administrator_user_id,
                    result=AUDIT_RESULT_SUCCESS,
                    changed_fields=(),
                    backup_name=None,
                )
                return PalworldSettingsSaveResult(
                    snapshot=inspected.snapshot,
                    changed_fields=(),
                    backup_name=None,
                )

            rendered = inspected.parsed.render(replacements)
            parse_ini(rendered)
            write_result = self._storage.write(
                expected_version=expected_version,
                content=rendered,
            )
            updated_snapshot = self._inspect(self._storage.read()).snapshot
            changed = tuple(changed_fields)
            self._audit(
                administrator_user_id,
                result=AUDIT_RESULT_SUCCESS,
                changed_fields=changed,
                backup_name=write_result.backup_name,
            )
            return PalworldSettingsSaveResult(
                snapshot=updated_snapshot,
                changed_fields=changed,
                backup_name=write_result.backup_name,
            )
        except SettingValueError as error:
            self._audit(
                administrator_user_id,
                result=AUDIT_RESULT_FAILURE,
                changed_fields=tuple(safe_requested_keys),
                error_kind="validation",
            )
            raise PalworldSettingsValidationError(str(error)) from error
        except (IniParseError, PalworldSettingsValidationError) as error:
            self._audit(
                administrator_user_id,
                result=AUDIT_RESULT_FAILURE,
                changed_fields=tuple(safe_requested_keys),
                error_kind="validation",
            )
            if isinstance(error, PalworldSettingsValidationError):
                raise
            raise PalworldSettingsValidationError(
                "O formato do PalWorldSettings.ini não pôde ser validado com segurança."
            ) from error
        except PalworldSettingsStorageError as error:
            self._audit(
                administrator_user_id,
                result=AUDIT_RESULT_FAILURE,
                changed_fields=tuple(safe_requested_keys),
                error_kind=error.kind.value,
            )
            raise

    def _inspect(self, stored: StoredSettings) -> _InspectedSettings:
        try:
            parsed = parse_ini(stored.content)
        except IniParseError as error:
            raise PalworldSettingsValidationError(
                "O formato do PalWorldSettings.ini não pôde ser validado com segurança."
            ) from error

        entries_by_key: dict[str, list[IniEntry]] = defaultdict(list)
        malformed_entries = 0
        for entry in parsed.entries:
            if entry.key is None:
                if entry.raw.strip():
                    malformed_entries += 1
                continue
            entries_by_key[entry.key].append(entry)

        fields_by_category: dict[str, list[SettingFieldView]] = defaultdict(list)
        fields: dict[str, SettingFieldView] = {}
        for key, definition in SETTING_DEFINITIONS_BY_KEY.items():
            entries = entries_by_key.get(key, [])
            if not entries:
                continue
            field = self._field_view(definition, entries)
            fields_by_category[definition.category].append(field)
            fields[key] = field

        categories = tuple(
            SettingCategoryView(name=category, fields=tuple(fields_by_category[category]))
            for category in SETTING_CATEGORIES
            if fields_by_category[category]
        )
        unknown_keys = tuple(
            sorted(key for key in entries_by_key if key not in SETTING_DEFINITIONS_BY_KEY)
        )
        return _InspectedSettings(
            parsed=parsed,
            snapshot=PalworldSettingsSnapshot(
                version=stored.version,
                categories=categories,
                unknown_keys=unknown_keys,
                malformed_entries=malformed_entries,
            ),
            fields=fields,
        )

    def _field_view(
        self,
        definition: SettingDefinition,
        entries: list[IniEntry],
    ) -> SettingFieldView:
        if len(entries) != 1:
            return _setting_field_view(
                definition,
                value=None,
                editable=False,
                issue="A chave aparece mais de uma vez e foi preservada sem edição.",
            )
        if definition.kind is SettingKind.SENSITIVE:
            return _setting_field_view(
                definition,
                value=None,
                editable=False,
                issue="Valor sensível ocultado e preservado sem edição.",
            )
        if definition.kind is SettingKind.READ_ONLY:
            return _setting_field_view(
                definition,
                value=None,
                editable=False,
                issue="Estrutura reconhecida e preservada sem edição nesta versão.",
            )
        raw_value = entries[0].value
        if raw_value is None:
            return _setting_field_view(
                definition,
                value=None,
                editable=False,
                issue="O valor não pôde ser interpretado e foi preservado.",
            )
        try:
            value = parse_setting_value(definition, raw_value)
        except SettingValueError:
            return _setting_field_view(
                definition,
                value=None,
                editable=False,
                issue="O valor usa um formato não suportado e foi preservado.",
            )
        return _setting_field_view(
            definition,
            value=value,
            editable=True,
            issue=None,
        )

    def _audit(
        self,
        administrator_user_id: int,
        *,
        result: str,
        changed_fields: tuple[str, ...],
        backup_name: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        details: dict[str, object] = {
            "changed_fields": list(changed_fields),
            "schema_version": PALWORLD_SETTINGS_SCHEMA_VERSION,
        }
        if backup_name is not None:
            details["backup_name"] = backup_name
        if error_kind is not None:
            details["error_kind"] = error_kind
        with session_scope(self._session_factory) as session:
            record_audit_event(
                session,
                occurred_at=self._clock(),
                action="PALWORLD_SETTINGS_UPDATE",
                result=result,
                origin=AUDIT_ORIGIN_ADMINISTRATOR,
                user_id=administrator_user_id,
                target="PalWorldSettings.ini",
                details=details,
            )
