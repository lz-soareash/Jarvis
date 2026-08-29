from pydantic import BaseModel, ConfigDict


class APIModel(BaseModel):
    """Modelo Pydantic base do JARVIS (consistente em todas as camadas)."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="ignore",
    )