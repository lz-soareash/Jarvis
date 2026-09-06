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


class ApprovalStatus(str, Enum):
    """Ciclo de vida de um pedido de aprovação (Fase 4 — Permissions)."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class MemoryKind(str, Enum):
    """Tipos de memória de longo prazo estruturada."""

    FACT = "fact"
    PREFERENCE = "preference"
    NOTE = "note"
    SUMMARY = "summary"


class TaskStatus(str, Enum):
    """Ciclo de vida de uma tarefa agêntica (Fase 13 — Agentic Core).

    Fluxo alvo: PLANNED → RUNNING → (por passo) → COMPLETED / FAILED.
    `CANCELLED` cobre interrupção; `COMPLETED` só chega após falhas parciais
    serem contornadas ou verificadas.
    """

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Status de um passo indiviliável dentro do plano de uma tarefa (Fase 13)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class DeviceType(str, Enum):
    """Tipos de dispositivo (Trusted Devices — fases futuras)."""

    DESKTOP = "desktop"
    MOBILE = "mobile"
    WEB = "web"


class DeviceStatus(str, Enum):
    """Fase 12.2 — ciclo de vida de um device remoto (identidade)."""

    PENDING = "pending"  # criado mas ainda não liberado para conexões
    ACTIVE = "active"  # pode autenticar (pairing bem-sucedido / bootstrap)
    REVOKED = "revoked"  # revogado — credenciais e sessões são invalidadas


class PairingStatus(str, Enum):
    """Fase 12.2 — ciclo de vida de um pedido de pairing.

    O código curto existe apenas derivado (hash). `CONSUMED` é terminal e
    single-use; `EXPIRED` cobre TTL e excesso de tentativas; `REVOKED` é
    revogação administrativa.
    """

    CREATED = "created"
    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class RemoteSessionStatus(str, Enum):
    """Fase 12.2 — ciclo de vida de uma sessão remota (separada do device)."""

    CREATED = "created"
    AUTHENTICATED = "authenticated"
    ACTIVE = "active"
    ENDED = "ended"
    REVOKED = "revoked"


class RemoteCommandStatus(str, Enum):
    """Fase 12.3 — ciclo de vida de um comando remoto (idempotência/rastreio)."""

    REGISTERED = "registered"  # chegou, ainda não avaliado
    PENDING_APPROVAL = "pending_approval"  # nível ≥ 2 aguardando decisão
    EXECUTING = "executing"  # em execução
    EXECUTED = "executed"  # concluído com sucesso
    FAILED = "failed"  # concluído com erro (incl. negação)
    INTERRUPTED = "interrupted"  # execução interrompida/resultado desconhecido
    EXPIRED = "expired"  # excedeu o TTL antes de concluir