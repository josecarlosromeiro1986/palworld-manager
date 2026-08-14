import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.palworld_settings.definitions import SettingDefinition, SettingKind

SECTION_NAME = "/Script/Pal.PalGameWorldSettings"
SECTION_PATTERN = re.compile(rf"(?m)^\s*\[{re.escape(SECTION_NAME)}\]\s*$")
NEXT_SECTION_PATTERN = re.compile(r"(?m)^\s*\[[^\]\r\n]+\]\s*$")
OPTION_PATTERN = re.compile(r"(?m)^[ \t]*OptionSettings[ \t]*=[ \t]*\(")
KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
MAX_STRING_LENGTH = 2048


class IniParseError(ValueError):
    pass


class SettingValueError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IniEntry:
    raw: str
    key: str | None
    value: str | None
    value_start: int | None
    value_end: int | None

    def replace_value(self, serialized: str) -> str:
        if self.value_start is None or self.value_end is None:
            raise IniParseError("entrada do INI não pode ser editada com segurança")
        return f"{self.raw[: self.value_start]}{serialized}{self.raw[self.value_end :]}"


@dataclass(frozen=True, slots=True)
class ParsedIni:
    prefix: str
    entries: tuple[IniEntry, ...]
    suffix: str

    def render(self, replacements: dict[str, str] | None = None) -> str:
        values = replacements or {}
        rendered_entries = [
            entry.replace_value(values[entry.key])
            if entry.key is not None and entry.key in values
            else entry.raw
            for entry in self.entries
        ]
        return f"{self.prefix}{','.join(rendered_entries)}{self.suffix}"


def parse_ini(content: str) -> ParsedIni:
    sections = list(SECTION_PATTERN.finditer(content))
    if len(sections) != 1:
        raise IniParseError(
            "o arquivo deve conter exatamente uma seção /Script/Pal.PalGameWorldSettings"
        )
    section = sections[0]
    next_section = NEXT_SECTION_PATTERN.search(content, section.end())
    section_end = next_section.start() if next_section is not None else len(content)
    section_content = content[section.end() : section_end]
    options = list(OPTION_PATTERN.finditer(section_content))
    if len(options) != 1:
        raise IniParseError("a seção deve conter exatamente um OptionSettings")
    option = options[0]
    opening_index = section.end() + option.end() - 1
    closing_index = _find_closing_parenthesis(content, opening_index)
    if closing_index >= section_end:
        raise IniParseError("OptionSettings ultrapassa os limites da seção")
    inner = content[opening_index + 1 : closing_index]
    entries = tuple(_parse_entry(raw) for raw in _split_entries(inner))
    return ParsedIni(
        prefix=content[: opening_index + 1],
        entries=entries,
        suffix=content[closing_index:],
    )


def _find_closing_parenthesis(content: str, opening_index: int) -> int:
    depth = 0
    quoted = False
    escaped = False
    for index in range(opening_index, len(content)):
        character = content[index]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
            if depth < 0:
                break
    raise IniParseError("OptionSettings possui parênteses ou aspas inválidos")


def _split_entries(inner: str) -> list[str]:
    if not inner:
        return []
    entries: list[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, character in enumerate(inner):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                raise IniParseError("valor composto possui parênteses inválidos")
            depth -= 1
        elif character == "," and depth == 0:
            entries.append(inner[start:index])
            start = index + 1
    if quoted or depth != 0:
        raise IniParseError("OptionSettings possui valor composto inválido")
    entries.append(inner[start:])
    return entries


def _parse_entry(raw: str) -> IniEntry:
    equal_index = _top_level_equal(raw)
    if equal_index is None:
        return IniEntry(raw=raw, key=None, value=None, value_start=None, value_end=None)
    key = raw[:equal_index].strip()
    if not KEY_PATTERN.fullmatch(key):
        return IniEntry(raw=raw, key=None, value=None, value_start=None, value_end=None)
    after_equal = raw[equal_index + 1 :]
    leading = len(after_equal) - len(after_equal.lstrip())
    trailing = len(after_equal) - len(after_equal.rstrip())
    value_start = equal_index + 1 + leading
    value_end = len(raw) - trailing if trailing else len(raw)
    if value_start >= value_end:
        return IniEntry(raw=raw, key=key, value="", value_start=value_start, value_end=value_end)
    return IniEntry(
        raw=raw,
        key=key,
        value=raw[value_start:value_end],
        value_start=value_start,
        value_end=value_end,
    )


def _top_level_equal(raw: str) -> int | None:
    depth = 0
    quoted = False
    escaped = False
    for index, character in enumerate(raw):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "=" and depth == 0:
            return index
    return None


def parse_setting_value(definition: SettingDefinition, raw: str) -> str:
    if definition.kind is SettingKind.BOOLEAN:
        normalized = raw.strip().lower()
        if normalized not in {"true", "false"}:
            raise SettingValueError("deve ser True ou False")
        return "True" if normalized == "true" else "False"
    if definition.kind is SettingKind.INTEGER:
        normalized = raw.strip()
        if not INTEGER_PATTERN.fullmatch(normalized):
            raise SettingValueError("deve ser um número inteiro")
        decimal_value = Decimal(normalized)
        _validate_range(definition, decimal_value)
        return normalized
    if definition.kind is SettingKind.NUMBER:
        normalized = raw.strip()
        try:
            decimal_value = Decimal(normalized)
        except InvalidOperation as error:
            raise SettingValueError("deve ser um número") from error
        if not decimal_value.is_finite():
            raise SettingValueError("deve ser um número finito")
        _validate_range(definition, decimal_value)
        return normalized
    if definition.kind is SettingKind.ENUM:
        string_value = _parse_string(raw)
        if string_value not in definition.options:
            raise SettingValueError("possui uma opção inválida")
        return string_value
    if definition.kind is SettingKind.STRING:
        string_value = _parse_string(raw)
        if len(string_value) > MAX_STRING_LENGTH:
            raise SettingValueError(f"deve ter no máximo {MAX_STRING_LENGTH} caracteres")
        if "\x00" in string_value or "\r" in string_value or "\n" in string_value:
            raise SettingValueError("não pode conter caracteres de controle ou quebras de linha")
        return string_value
    raise SettingValueError("não é editável")


def serialize_setting_value(definition: SettingDefinition, value: str) -> str:
    normalized = parse_setting_value(definition, value)
    if definition.kind is SettingKind.STRING:
        return json.dumps(normalized, ensure_ascii=False)
    return normalized


def _parse_string(raw: str) -> str:
    normalized = raw.strip()
    if not normalized.startswith('"'):
        return normalized
    try:
        value = json.loads(normalized)
    except json.JSONDecodeError as error:
        raise SettingValueError("possui texto entre aspas inválido") from error
    if not isinstance(value, str):
        raise SettingValueError("deve ser texto")
    return value


def _validate_range(definition: SettingDefinition, value: Decimal) -> None:
    if definition.minimum is not None and value < definition.minimum:
        raise SettingValueError(f"deve ser maior ou igual a {definition.minimum}")
    if definition.maximum is not None and value > definition.maximum:
        raise SettingValueError(f"deve ser menor ou igual a {definition.maximum}")
