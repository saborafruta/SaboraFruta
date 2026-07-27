"""
MDFe — Manifesto Eletrônico de Documentos Fiscais (modelo 58).
"""
from __future__ import annotations

from typing import Any, Dict

from ._authorized_doc import AuthorizedDocResource


class MDFeResource(AuthorizedDocResource):
    endpoint = "mdfe"
    supports_carta_correcao = False

    def encerrar(
        self,
        ref: str,
        *,
        nome_municipio: str,
        sigla_uf: str,
        data: str,
    ) -> Dict[str, Any]:
        """Encerra um MDFe quando o transporte chega ao destino."""
        body = {
            "nome_municipio": nome_municipio,
            "sigla_uf": sigla_uf,
            "data": data,
        }
        return self._http.post(f"/v2/mdfe/{ref}/encerrar", json_body=body)

    def incluir_condutor(self, ref: str, nome: str, cpf: str) -> Dict[str, Any]:
        """Inclui um condutor adicional no MDFe."""
        return self._http.post(
            f"/v2/mdfe/{ref}/inclusao_condutor",
            json_body={"nome": nome, "cpf": cpf},
        )

    def incluir_dfe(self, ref: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Inclui DF-e (NFe/CTe) em MDFe já autorizado."""
        return self._http.post(f"/v2/mdfe/{ref}/inclusao_dfe", json_body=payload)

    def baixar_damdfe(self, ref: str) -> bytes:
        return self.baixar_pdf(ref)
