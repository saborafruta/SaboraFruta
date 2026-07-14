"""
Resource Focus NFe — Gestao de empresas (cadastro / certificado / CSC).

Endpoints:
  POST  /v2/empresas          — cria ou atualiza a empresa (upsert via CNPJ)
  GET   /v2/empresas?cnpj=... — lista empresas (filtra por CNPJ)
  GET   /v2/empresas/{id}     — busca empresa por ID interno Focus
  PUT   /v2/empresas/{id}     — atualiza empresa existente (certif, CSC, etc.)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._base import ResourceBase


class EmpresasResource(ResourceBase):

    def criar(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._http.post("/v2/empresas", json_body=payload)

    def listar(self, cnpj: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {}
        if cnpj:
            params["cnpj"] = cnpj
        result = self._http.get("/v2/empresas", params=params or None)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("empresas", [result])
        return []

    def obter(self, empresa_id: str) -> Dict[str, Any]:
        return self._http.get(f"/v2/empresas/{empresa_id}")

    def atualizar(self, empresa_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._http.put(f"/v2/empresas/{empresa_id}", json_body=payload)

    def upsert(self, cnpj: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cria a empresa se não existir; atualiza se já estiver cadastrada.
        `payload` deve incluir `cnpj`. Retorna o dict retornado pela Focus.
        """
        empresas = self.listar(cnpj=cnpj)
        if empresas:
            empresa_id = str(empresas[0].get("id") or empresas[0].get("cnpj", ""))
            if empresa_id:
                return self.atualizar(empresa_id, payload)
        return self.criar(payload)
