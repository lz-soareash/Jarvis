"""Cliente HTTP dedicado para o Atlas (Fase 10).

O Atlas é um projeto Django separado, consumido pelo JARVIS como serviço
externo via HTTP/JWT, seguindo o contrato documentado em
`docs/JARVIS_INTEGRATION.md` do projeto Atlas.

Resumo do contrato usado aqui:

- Login:  `POST {base}/api/auth/token/`  { email, password } -> { access, refresh }
- Refresh:`POST {base}/api/auth/token/refresh/` { refresh } -> { access, refresh }
- Chat:   `POST {base}/api/assistant/chat/`     { messages } -> { answer, sources, ... }

Nenhum segredo (email/senha/access) é exposto em logs. Timeout e tratamento de
erros isolam o JARVIS da indisponibilidade do Atlas.
"""

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.atlas import AtlasChatResponse

logger = logging.getLogger("jarvis.atlas")

_PATH_TOKEN = "/api/auth/token/"
_PATH_TOKEN_REFRESH = "/api/auth/token/refresh/"
_PATH_CHAT = "/api/assistant/chat/"


class AtlasError(RuntimeError):
    """Erro de comunicação/configuração com o Atlas."""


class AtlasUnavailable(AtlasError):
    """Atlas recebeu pedido mas não pôde responder (conexão/timeout/HTTP)."""


class AtlasAuthError(AtlasError):
    """Falha de autenticação com o Atlas (credenciais inválidas)."""


class AtlasConfigError(AtlasError):
    """Atlas habilitado mas faltando configuração (ex.: sem credenciais)."""


class AtlasClient:
    """Cliente thread-safe das rotas do Atlas, com cache de token JWT.

    Usa um client httpx compartilhado; o login é feito sob demanda e o `access`
    renovado via `refresh` quando expira (HTTP 401). Credenciais ficam apenas em
    memória e nunca são logadas.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or settings.atlas_base_url).rstrip("/")
        self.email = email if email is not None else settings.atlas_email
        self.password = password if password is not None else settings.atlas_password
        self.timeout = timeout if timeout is not None else settings.atlas_timeout
        self._access: str | None = None
        self._refresh: str | None = None
        self._http = httpx.Client(timeout=self.timeout, transport=transport)

    # ------------------------------------------------------------------
    # Configuração
    # ------------------------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self.email and self.password)

    # ------------------------------------------------------------------
    # Erros
    # ------------------------------------------------------------------
    def _raise_http(self, resp: httpx.Response) -> None:
        status = resp.status_code
        if status == 401:
            raise AtlasAuthError("Autenticação com o Atlas falhou (401).")
        if status == 429:
            raise AtlasUnavailable("Atlas atingiu limite de chamadas (429).")
        if status == 502:
            raise AtlasUnavailable("Provedor de IA do Atlas falhou (502).")
        raise AtlasUnavailable(f"Atlas respondeu com HTTP {status}.")

    # ------------------------------------------------------------------
    # Autenticação (JWT) — sem exposição de segredos
    # ------------------------------------------------------------------
    def _login(self) -> None:
        if not self.is_configured:
            raise AtlasConfigError(
                "ATLAS_EMAIL/ATLAS_PASSWORD não configuradas (arquivo .env)."
            )
        try:
            resp = self._http.post(
                self.base_url + _PATH_TOKEN, json={"email": self.email, "password": self.password}
            )
        except httpx.HTTPError as exc:
            raise AtlasUnavailable(f"Falha de conexão com o Atlas: {exc}") from exc

        if resp.status_code != 200:
            self._raise_http(resp)

        data = self._json_or_unavailable(resp, "token")
        self._access = data.get("access")
        self._refresh = data.get("refresh")
        if not self._access:
            raise AtlasAuthError("Resposta de token do Atlas inválida (sem access).")

    def _refresh_access(self) -> None:
        if not self._refresh:
            self._login()
            return
        try:
            resp = self._http.post(
                self.base_url + _PATH_TOKEN_REFRESH, json={"refresh": self._refresh}
            )
        except httpx.HTTPError as exc:
            raise AtlasUnavailable(f"Falha de conexão com o Atlas (refresh): {exc}") from exc

        if resp.status_code != 200:
            # Token de refresh inválido/expirado -> refazer login completo.
            self._clear_token()
            self._login()
            return
        data = self._json_or_unavailable(resp, "refresh")
        if data.get("access"):
            self._access = data["access"]
        if data.get("refresh"):
            self._refresh = data["refresh"]

    def _clear_token(self) -> None:
        self._access = None
        self._refresh = None

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access}"}

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, Any]]) -> AtlasChatResponse:
        """Envia o histórico ao `/assistant/chat/` do Atlas.

        `messages`: lista [{role, content}]. Retorna a resposta tipada do Atlas.
        Levanta AtlasUnavailable/AtlasAuthError/AtlasConfigError em falhas.
        """
        if not self._access:
            self._login()
        if not self._access:  # pragma: no cover — defensivo
            raise AtlasAuthError("Autenticação com o Atlas falhou.")

        try:
            resp = self._http.post(
                self.base_url + _PATH_CHAT,
                json={"messages": messages},
                headers=self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise AtlasUnavailable(f"Falha de conexão com o Atlas: {exc}") from exc

        if resp.status_code == 401:
            # Access expirou -> tenta renovar uma vez e repete.
            self._refresh_access()
            return self._retry_chat(messages)

        if resp.status_code != 200:
            self._raise_http(resp)

        return self._parse_chat(resp)

    def _retry_chat(self, messages: list[dict[str, Any]]) -> AtlasChatResponse:
        """Repete o chat após renovar o token (chamado só em 401)."""
        if not self._access:  # pragma: no cover — defensivo
            raise AtlasAuthError("Autenticação com o Atlas falhou.")
        try:
            resp = self._http.post(
                self.base_url + _PATH_CHAT,
                json={"messages": messages},
                headers=self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise AtlasUnavailable(f"Falha de conexão com o Atlas: {exc}") from exc
        if resp.status_code != 200:
            self._raise_http(resp)
        return self._parse_chat(resp)

    @staticmethod
    def _json_or_unavailable(resp: httpx.Response, what: str) -> dict[str, Any]:
        """Decodifica o body JSON de uma resposta, ou trata como indisponível.

        Evita que um corpo não-JSON (ex.: HTML de erro de servidor) vaze
        `json.JSONDecodeError` para o chamador; roda o fluxo de fallback.
        """
        try:
            data = resp.json()
        except ValueError as exc:
            raise AtlasUnavailable(
                f"Resposta {what} do Atlas não é JSON válido."
            ) from exc
        if not isinstance(data, dict):
            raise AtlasUnavailable(
                f"Resposta {what} do Atlas em formato inesperado."
            )
        return data

    @staticmethod
    def _parse_chat(resp: httpx.Response) -> AtlasChatResponse:
        payload = AtlasClient._json_or_unavailable(resp, "chat")
        return AtlasChatResponse.model_validate(payload)

    def auth_test(self) -> None:
        """Valida URL + credenciais realizando um login real.

        Não retorna segredos; levanta AtlasUnavailable/AtlasAuthError em falha.
        """
        self._login()

    def close(self) -> None:
        self._http.close()
_client: AtlasClient | None = None


def get_atlas_client() -> AtlasClient:
    """Singleton do AtlasClient (thread-safety é responsabilidade do chamador)."""
    global _client
    if _client is None:
        _client = AtlasClient()
    return _client


def set_atlas_client(client: AtlasClient | None) -> None:
    """Substitui o client singleton (usado em testes com transporte mock)."""
    global _client
    if _client is not None and _client is not client:
        _client.close()
    _client = client


def reset_atlas_client() -> None:
    """Fecha e descarta o client (usado em testes para isolar estado)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None
