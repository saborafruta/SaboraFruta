"""
Base HTTP client para a API Focus NFe.

Concentra:
  - autenticação Basic (token como username, senha vazia)
  - construção de URLs absolutas a partir de paths relativos
  - parsing de respostas (JSON / binário)
  - retry simples para 5xx e timeouts
  - mapeamento de status code -> exceção tipada
  - logging estruturado opcional
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Union

import requests
from requests.auth import HTTPBasicAuth

from .config import FocusNFeConfig
from .exceptions import (
    FocusNFeAuthError,
    FocusNFeError,
    FocusNFeNetworkError,
    FocusNFeNotFoundError,
    FocusNFeProcessingError,
    FocusNFeRateLimitError,
    FocusNFeServerError,
    FocusNFeValidationError,
)

logger = logging.getLogger(__name__)

# Status -> Exceção
_STATUS_EXCEPTIONS = {
    400: FocusNFeValidationError,
    401: FocusNFeAuthError,
    403: FocusNFeAuthError,
    404: FocusNFeNotFoundError,
    422: FocusNFeProcessingError,
    429: FocusNFeRateLimitError,
}

# Códigos retentáveis
_RETRYABLE_STATUS = {500, 502, 503, 504}


class BaseAPIClient:
    """Camada HTTP da integração. Não conhece endpoints específicos."""

    def __init__(
        self,
        config: Optional[FocusNFeConfig] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config or FocusNFeConfig.from_env()
        self._session = session or requests.Session()
        self._session.auth = HTTPBasicAuth(self.config.token, "")
        self._session.headers.update({
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
        })

    # ---------------------------------------------------------------- HTTP
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        data: Any = None,
        binary: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Union[Dict[str, Any], bytes, str, None]:
        """
        Executa um request e retorna:
            - dict (JSON parseado) por padrão
            - bytes se binary=True (PDFs/XMLs)
            - None em respostas 204
        """
        url = self._build_url(path)
        headers = dict(extra_headers or {})

        last_exc: Optional[BaseException] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json_body,
                    data=data,
                    headers=headers,
                    timeout=self.config.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = e
                logger.warning(
                    "FocusNFe: erro de rede (tentativa %d/%d) em %s %s: %s",
                    attempt + 1, self.config.max_retries + 1, method, url, e,
                )
                if attempt < self.config.max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise FocusNFeNetworkError(f"Falha de rede em {method} {url}: {e}") from e
            except requests.RequestException as e:
                raise FocusNFeNetworkError(f"Falha em {method} {url}: {e}") from e

            # Retry em 5xx
            if resp.status_code in _RETRYABLE_STATUS and attempt < self.config.max_retries:
                logger.warning(
                    "FocusNFe: %d em %s %s — tentando novamente (%d/%d)",
                    resp.status_code, method, url, attempt + 1, self.config.max_retries,
                )
                time.sleep(min(2 ** attempt, 8))
                continue

            return self._parse_response(resp, binary=binary)

        # não deveria chegar aqui
        raise FocusNFeNetworkError(f"Esgotadas tentativas: {last_exc}")

    # Atalhos
    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> Any:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> Any:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)

    # ---------------------------------------------------------------- helpers
    def _build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.config.base_url}{path}"

    def _parse_response(self, resp: requests.Response, *, binary: bool) -> Any:
        status = resp.status_code

        # Sucesso
        if 200 <= status < 300:
            if status == 204 or not resp.content:
                return None
            if binary:
                return resp.content
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype or resp.content[:1] in (b"{", b"["):
                try:
                    return resp.json()
                except json.JSONDecodeError:
                    return resp.text
            return resp.content if "application/pdf" in ctype or "octet-stream" in ctype else resp.text

        # Erro: pega JSON quando possível
        body_json: Any = None
        try:
            body_json = resp.json()
        except (ValueError, json.JSONDecodeError):
            body_json = None

        message = self._error_message(body_json, resp.text)
        exc_cls = _STATUS_EXCEPTIONS.get(status, FocusNFeServerError if status >= 500 else FocusNFeError)
        raise exc_cls(
            message,
            status_code=status,
            response_json=body_json,
            response_text=resp.text[:2000] if resp.text else None,
        )

    @staticmethod
    def _error_message(body_json: Any, fallback_text: str) -> str:
        if isinstance(body_json, dict):
            base = ""
            for key in ("mensagem", "message", "erro", "error", "mensagem_sefaz"):
                if key in body_json and body_json[key]:
                    base = str(body_json[key])
                    break
            # Erros de schema (422) trazem o detalhamento campo a campo em
            # "erros" — sem isso, a mensagem fica so no generico
            # "Erro na validacao do Schema XML, verifique o detalhamento".
            detalhes = BaseAPIClient._formatar_erros_detalhados(body_json.get("erros"))
            if base and detalhes:
                return f"{base} — {detalhes}"
            return base or detalhes or (fallback_text or "Erro Focus NFe (sem mensagem)")
        return fallback_text or "Erro Focus NFe (sem mensagem)"

    @staticmethod
    def _formatar_erros_detalhados(erros: Any) -> str:
        """Achata o array `erros` da Focus em texto legivel (campo: mensagem)."""
        if not erros:
            return ""
        if isinstance(erros, dict):
            erros = [erros]
        partes = []
        for e in erros:
            if isinstance(e, dict):
                campo = e.get("campo") or e.get("field") or e.get("path") or ""
                msg = e.get("mensagem") or e.get("message") or e.get("erro") or e.get("descricao") or ""
                if campo and msg:
                    partes.append(f"{campo}: {msg}")
                else:
                    partes.append(str(msg or campo or e))
            else:
                partes.append(str(e))
        return "; ".join(p for p in partes if p)


class ResourceBase:
    """Base para resources (NFe, NFCe, etc.). Cada resource recebe o BaseAPIClient."""

    def __init__(self, http: BaseAPIClient) -> None:
        self._http = http

    @property
    def http(self) -> BaseAPIClient:
        return self._http
