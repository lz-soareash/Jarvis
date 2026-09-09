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
    """Tipos de memória de longo prazo estruturada (Fase 19.5).

    Categorias da política de escrita da VEGA: efêmero, preferência, fato,
    projeto, tarefa, conhecimento e decisão. Valores são aditivos — bancos
    existentes com `fact/preference/note/summary` continuam válidos.
    """

    EPHEMERAL = "ephemeral"  # transitório/piloto — expira (TTL deliberado)
    PREFERENCE = "preference"  # preferência do usuário (longo prazo)
    FACT = "fact"  # fato/observação (padrão, compatível com fases anteriores)
    PROJECT = "project"  # contexto de um projeto do usuário
    TASK = "task"  # estado/contexto de uma tarefa em andamento
    KNOWLEDGE = "knowledge"  # conhecimento validado (espelho do ledger local)
    DECISION = "decision"  # decisão tomada (ferramenta/stack/política)
    NOTE = "note"  # nota livre do usuário
    SUMMARY = "summary"  # resumo rolante de uma sessão


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
    """Fase 12.2 + 21 — ciclo de vida de um device remoto (identidade).

    Estados de confiança (Device Trust): `PENDING` (registrado, aguardando
    pareamento) → `PAIRED` (credencial emitida) → `ACTIVE` (em uso); revogações
    vão para `REVOKED`; `EXPIRED` cobre sessões/credenciais vencidas. `PAIRED` e
    `ACTIVE` são confiáveis para autenticar (`trusted_statuses`); incrementos
    são aditivos — bases antigas com apenas `PENDING/ACTIVE/REVOKED` seguem válidas.
    """

    PENDING = "pending"  # registrado mas ainda não pareado (aguarda código)
    PAIRED = "paired"  # pareado (credencial emitida) — confiável
    ACTIVE = "active"  # em uso ativo — confiável (pairing bem-sucedido / bootstrap)
    REVOKED = "revoked"  # revogado — credenciais e sessões são invalidadas
    EXPIRED = "expired"  # credencial/sessão expirou (dispositivo desatualizado)


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


class AtlasWriteMode(str, Enum):
    """Política de escrita de conhecimento no Atlas (Fase 14).

    Default conservador (`SUGGEST`): nada é persistido sem decisão/visibilidade
    do usuário. `AUTO` habilita escrita automática após dedup/conflito.
    """

    DISABLED = "disabled"  # nunca escreve no Atlas
    SUGGEST = "suggest"  # só apresenta candidatos (non-destructive)
    AUTO = "auto"  # persiste candidatos validados automaticamente
    USER_CONFIRMED = "user_confirmed"  # persiste apenas o que o usuário confirmar


class KnowledgeConfidence(str, Enum):
    """Proveniência/confiança de um KnowledgeCandidate (Fase 14 — section 20).

    `MODEL_INFERRED` JAMAIS é tratado como fato: a persistência exige
    confiança >= `SOURCE_CONFIRMED` (ou confirmação explícita do usuário).
    """

    UNVERIFIED = "unverified"  # sem origem verificável
    SOURCE_CONFIRMED = "source_confirmed"  # 1 fonte coletada
    MULTI_SOURCE_CONFIRMED = "multi_source_confirmed"  # 2+ fontes independentes
    MODEL_INFERRED = "model_inferred"  # dedução do modelo, não verificada
    USER_CONFIRMED = "user_confirmed"  # validado explicitamente pelo usuário


class RemoteCommandStatus(str, Enum):
    """Fase 12.3 — ciclo de vida de um comando remoto (idempotência/rastreio)."""

    REGISTERED = "registered"  # chegou, ainda não avaliado
    PENDING_APPROVAL = "pending_approval"  # nível ≥ 2 aguardando decisão
    EXECUTING = "executing"  # em execução
    EXECUTED = "executed"  # concluído com sucesso
    FAILED = "failed"  # concluído com erro (incl. negação)
    INTERRUPTED = "interrupted"  # execução interrompida/resultado desconhecido
    EXPIRED = "expired"  # excedeu o TTL antes de concluir