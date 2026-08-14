from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

PALWORLD_SETTINGS_SCHEMA_VERSION = "1.0.3"
PALWORLD_SETTINGS_SCHEMA_SOURCE = (
    "https://docs.palworldgame.com/settings-and-operation/configuration/"
)


class SettingKind(StrEnum):
    BOOLEAN = "boolean"
    ENUM = "enum"
    INTEGER = "integer"
    NUMBER = "number"
    READ_ONLY = "read_only"
    SENSITIVE = "sensitive"
    STRING = "string"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: str
    label: str
    description: str
    category: str
    kind: SettingKind
    options: tuple[str, ...] = ()
    minimum: Decimal | None = None
    maximum: Decimal | None = None


def _definition(
    key: str,
    label: str,
    description: str,
    category: str,
    kind: SettingKind,
    *,
    options: tuple[str, ...] = (),
    minimum: int | str | None = None,
    maximum: int | str | None = None,
) -> SettingDefinition:
    return SettingDefinition(
        key=key,
        label=label,
        description=description,
        category=category,
        kind=kind,
        options=options,
        minimum=Decimal(str(minimum)) if minimum is not None else None,
        maximum=Decimal(str(maximum)) if maximum is not None else None,
    )


PERFORMANCE = "Desempenho"
MANAGEMENT = "Administração do servidor"
FEATURES = "Recursos"
BALANCE = "Balanceamento"

SETTING_DEFINITIONS = (
    _definition(
        "BaseCampMaxNum",
        "Total de bases",
        "Quantidade total de bases no servidor.",
        PERFORMANCE,
        SettingKind.INTEGER,
    ),
    _definition(
        "BaseCampMaxNumInGuild",
        "Bases por guilda",
        "Quantidade máxima de bases por guilda.",
        PERFORMANCE,
        SettingKind.INTEGER,
        maximum=10,
    ),
    _definition(
        "BaseCampWorkerMaxNum",
        "Pals por base",
        "Quantidade máxima de Pals trabalhando em cada base.",
        PERFORMANCE,
        SettingKind.INTEGER,
        maximum=50,
    ),
    _definition(
        "MaxBuildingLimitNum",
        "Construções por jogador",
        "Limite de construções por jogador; zero significa ilimitado.",
        PERFORMANCE,
        SettingKind.INTEGER,
    ),
    _definition(
        "PhysicsActiveDropItemMaxNum",
        "Itens com física",
        "Quantidade máxima de itens largados que usam física.",
        PERFORMANCE,
        SettingKind.INTEGER,
    ),
    _definition(
        "ServerReplicatePawnCullDistance",
        "Distância de sincronização",
        "Distância de sincronização dos Pals em centímetros.",
        PERFORMANCE,
        SettingKind.NUMBER,
        minimum=5000,
        maximum=15000,
    ),
    _definition(
        "bAllowClientMod",
        "Permitir clientes com mods",
        "Permite a entrada de jogadores com mods habilitados.",
        MANAGEMENT,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bEnableBuildingPlayerUIdDisplay",
        "Exibir criador das construções",
        "Exibe o identificador do jogador que criou cada estrutura.",
        MANAGEMENT,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bIsShowJoinLeftMessage",
        "Mensagens de entrada e saída",
        "Exibe mensagens quando jogadores entram ou saem.",
        MANAGEMENT,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bIsUseBackupSaveData",
        "Backups internos do Palworld",
        "Habilita os backups internos do mundo feitos pelo Palworld.",
        MANAGEMENT,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "ChatPostLimitPerMinute",
        "Mensagens de chat por minuto",
        "Limite de mensagens de chat por minuto.",
        MANAGEMENT,
        SettingKind.INTEGER,
    ),
    _definition(
        "LogFormatType",
        "Formato dos logs",
        "Formato emitido pelo servidor.",
        MANAGEMENT,
        SettingKind.ENUM,
        options=("Text", "Json"),
    ),
    _definition(
        "PublicIP",
        "IP público",
        "IP externo anunciado pelo servidor comunitário.",
        MANAGEMENT,
        SettingKind.STRING,
    ),
    _definition(
        "PublicPort",
        "Porta pública",
        "Porta externa anunciada pelo servidor comunitário.",
        MANAGEMENT,
        SettingKind.INTEGER,
        minimum=1,
        maximum=65535,
    ),
    _definition(
        "RCONEnabled",
        "Habilitar RCON",
        "Habilita o protocolo RCON.",
        MANAGEMENT,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "RCONPort",
        "Porta RCON",
        "Porta usada pelo RCON.",
        MANAGEMENT,
        SettingKind.INTEGER,
        minimum=1,
        maximum=65535,
    ),
    _definition(
        "RESTAPIEnabled",
        "Habilitar REST API",
        "Habilita a REST API oficial usada pelo Manager.",
        MANAGEMENT,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "RESTAPIPort",
        "Porta da REST API",
        "Porta de escuta da REST API oficial.",
        MANAGEMENT,
        SettingKind.INTEGER,
        minimum=1,
        maximum=65535,
    ),
    _definition(
        "ServerDescription",
        "Descrição do servidor",
        "Descrição apresentada pelo servidor.",
        MANAGEMENT,
        SettingKind.STRING,
    ),
    _definition(
        "ServerName",
        "Nome do servidor",
        "Nome apresentado pelo servidor.",
        MANAGEMENT,
        SettingKind.STRING,
    ),
    _definition(
        "ServerPlayerMaxNum",
        "Máximo de jogadores",
        "Quantidade máxima de jogadores conectados.",
        MANAGEMENT,
        SettingKind.INTEGER,
    ),
    _definition(
        "AdminPassword",
        "Senha administrativa",
        "Campo sensível preservado sem exibir seu valor.",
        MANAGEMENT,
        SettingKind.SENSITIVE,
    ),
    _definition(
        "ServerPassword",
        "Senha do servidor",
        "Campo sensível preservado sem exibir seu valor.",
        MANAGEMENT,
        SettingKind.SENSITIVE,
    ),
    _definition(
        "CrossplayPlatforms",
        "Plataformas crossplay",
        "Estrutura composta reconhecida e preservada sem edição nesta versão.",
        MANAGEMENT,
        SettingKind.READ_ONLY,
    ),
    _definition(
        "bEnableFastTravel",
        "Permitir viagem rápida",
        "Habilita a viagem rápida.",
        FEATURES,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bEnableInvaderEnemy",
        "Habilitar invasões",
        "Habilita inimigos invasores.",
        FEATURES,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bEnableVoiceChat",
        "Habilitar chat de voz",
        "Habilita o chat de voz do jogo.",
        FEATURES,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bIsPvP",
        "Habilitar PvP",
        "Habilita o modo PvP.",
        FEATURES,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "bShowPlayerList",
        "Exibir lista de jogadores",
        "Exibe a lista de jogadores no menu do jogo.",
        FEATURES,
        SettingKind.BOOLEAN,
    ),
    _definition(
        "RandomizerType",
        "Modo de aleatorização",
        "Modo de aleatorização do surgimento de Pals.",
        FEATURES,
        SettingKind.ENUM,
        options=("None", "Region", "All"),
    ),
    _definition(
        "RandomizerSeed",
        "Seed da aleatorização",
        "Seed usada pelo modo de aleatorização.",
        FEATURES,
        SettingKind.INTEGER,
    ),
    _definition(
        "DayTimeSpeedRate",
        "Velocidade do dia",
        "Multiplicador da passagem do tempo durante o dia.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "NightTimeSpeedRate",
        "Velocidade da noite",
        "Multiplicador da passagem do tempo durante a noite.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "ExpRate",
        "Experiência",
        "Multiplicador de experiência recebida.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PalCaptureRate",
        "Captura de Pals",
        "Multiplicador da chance de captura.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PalSpawnNumRate",
        "Surgimento de Pals",
        "Multiplicador da quantidade de Pals; pode afetar o desempenho.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PalDamageRateAttack",
        "Dano causado por Pals",
        "Multiplicador do dano causado por Pals.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PalDamageRateDefense",
        "Dano recebido por Pals",
        "Multiplicador do dano recebido por Pals.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PlayerDamageRateAttack",
        "Dano causado por jogadores",
        "Multiplicador do dano causado por jogadores.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PlayerDamageRateDefense",
        "Dano recebido por jogadores",
        "Multiplicador do dano recebido por jogadores.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "PalEggDefaultHatchingTime",
        "Tempo de incubação",
        "Horas para incubar um ovo enorme.",
        BALANCE,
        SettingKind.NUMBER,
    ),
    _definition(
        "GuildPlayerMaxNum",
        "Jogadores por guilda",
        "Quantidade máxima de jogadores em uma guilda.",
        BALANCE,
        SettingKind.INTEGER,
    ),
    _definition(
        "SupplyDropSpan",
        "Intervalo de suprimentos",
        "Intervalo em minutos para meteoritos e suprimentos.",
        BALANCE,
        SettingKind.INTEGER,
    ),
    _definition(
        "DeathPenalty",
        "Penalidade por morte",
        "Itens e Pals perdidos após a morte.",
        BALANCE,
        SettingKind.ENUM,
        options=("None", "Item", "ItemAndEquipment", "All"),
    ),
)

SETTING_DEFINITIONS_BY_KEY = {definition.key: definition for definition in SETTING_DEFINITIONS}
SETTING_CATEGORIES = (PERFORMANCE, MANAGEMENT, FEATURES, BALANCE)
