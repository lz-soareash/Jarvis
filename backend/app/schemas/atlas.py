"""Modelos da resposta do Atlas (Fase 10).

Espelham o contrato HTTP/JWT documentado no projeto Atlas em
`docs/JARVIS_INTEGRATION.md` e a resposta real do
`POST /api/assistant/chat/` (ChatService do Atlas). Campos são tolerantes a
ausência (`extra="ignore"` + padrões) para não quebrar se o Atlas evoluir.
"""

from app.schemas.base import APIModel


class AtlasClassification(APIModel):
    kind: str = ""
    label: str = ""
    source_based: bool = False


class AtlasSource(APIModel):
    id: str | None = None
    entity: str | None = None
    label: str | None = None
    title: str | None = None
    route: str | None = None
    score: float | None = None


class AtlasProposal(APIModel):
    id: str | None = None
    tool: str | None = None
    entity: str | None = None
    summary: str | None = None
    payload: dict | None = None
    status: str | None = None
    status_label: str | None = None
    created_at: str | None = None


class AtlasAgentRun(APIModel):
    id: str | None = None
    query: str | None = None
    status: str | None = None
    iterations: int | None = None
    steps: list | None = None
    created_at: str | None = None


class AtlasChatResponse(APIModel):
    answer: str = ""
    sources: list[AtlasSource] = []
    provider: str = "atlas"
    classification: AtlasClassification = AtlasClassification()
    semantic_available: bool = False
    proposals: list[AtlasProposal] = []
    agent_run: AtlasAgentRun | None = None


class AtlasChatMeta(APIModel):
    """Metadados persistidos junto à mensagem do assistente no JARVIS."""

    source: str = "atlas"
    provider: str = "atlas"
    classification: AtlasClassification | None = None
    sources: list[AtlasSource] = []
    proposals: list[AtlasProposal] = []
    agent_run: AtlasAgentRun | None = None
    semantic_available: bool = False
