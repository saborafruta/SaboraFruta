"""Backups mensais de documentos emitidos pela Focus NFe."""
from __future__ import annotations

from typing import Any, Dict, List

from .._base import ResourceBase


class BackupsResource(ResourceBase):
    """Lista e baixa os ZIPs oficiais de XML por CNPJ."""

    def listar(self, cnpj: str) -> List[Dict[str, Any]]:
        resultado = self._http.get(f"/v2/backups/{cnpj}.json")
        if isinstance(resultado, list):
            return [item for item in resultado if isinstance(item, dict)]
        return []

    def baixar_xmls(self, url: str) -> bytes:
        resultado = self._http.get(url, binary=True)
        return resultado if isinstance(resultado, bytes) else b""
