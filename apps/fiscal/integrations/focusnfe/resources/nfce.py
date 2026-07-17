"""
Recurso NFCe (Nota Fiscal de Consumidor Eletrônica modelo 65).
"""
from __future__ import annotations

from typing import Any, Dict

from ._authorized_doc import AuthorizedDocResource


class NFCeResource(AuthorizedDocResource):
    endpoint = "nfce"
    supports_carta_correcao = False  # NFCe não tem CC-e
    supports_inutilizacao = True

    def autorizar_offline(self, ref: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Emite NFC-e em contingencia offline quando a SEFAZ estiver indisponivel."""
        return self._http.post(
            "/v2/nfce",
            params={"ref": ref, "forma_emissao": "offline"},
            json_body=payload,
        )

    def baixar_danfce(self, ref: str) -> bytes:
        """Alias para baixar_pdf — DANFE NFCe."""
        return self.baixar_pdf(ref)
