from enum import Enum, IntEnum


class AgentState(str, Enum):
    """Máquina de estados do agente (seguida pela interface)."""

    IDLE = "idle"
    THINKING = "thinking"
    PLANNING = "planning"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXECUTING = "executing"
    OBSERVING = "observing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PermissionLevel(IntEnum):
    """Níveis de permissão de ferramentas, independentes da IA."""

    LEVEL_0 = 0  # Leitura segura (CPU, RAM, processos...) — automática
    LEVEL_1 = 1  # Ação reversível (abrir app/arquivo) — configurável
    LEVEL_2 = 2  # Alteração (editar/escrever arquivo, executar código) — confirmação
    LEVEL_3 = 3  # Potencialmente destrutivo — bloqueado por padrão

    @property
    def requires_confirmation(self) -> bool:
        return self >= self.LEVEL_2

    @property
    def blocked_by_default(self) -> bool:
        return self >= self.LEVEL_3


class RiskLevel(str, Enum):
    """Risco apresentado ao usuário nas confirmações."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryKind(str, Enum):
    """Tipos de memória de longo prazo estruturada."""

    FACT = "fact"
    PREFERENCE = "preference"
    NOTE = "note"
    SUMMARY = "summary"


class DeviceType(str, Enum):
    """Tipos de dispositivo (Trusted Devices — fases futuras)."""

    DESKTOP = "desktop"
    MOBILE = "mobile"
    WEB = "web"