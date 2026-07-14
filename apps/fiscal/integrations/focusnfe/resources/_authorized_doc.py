"""
Mixin com operações comuns a documentos eletrônicos com autorização SEFAZ:
    NFe, NFCe, CTe, CTeOS, NFCOM, MDFe.

Cada subclasse define `endpoint` (ex.: "nfe", "nfce", "cte" ...).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .._base import ResourceBase


class AuthorizedDocResource(ResourceBase):
    endpoint: str = ""        # ex.: "nfe"
    supports_carta_correcao: bool = False
    supports_inutilizacao: bool = False
    supports_email: bool = True

    # ----------------------------------------------------- emissão / consulta
    def autorizar(self, ref: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Envia o documento para autorização. `ref` é seu identificador interno."""
        return self._http.post(f"/v2/{self.endpoint}", params={"ref": ref}, json_body=payload)

    def consultar(self, ref: str, completa: bool = False) -> Dict[str, Any]:
        """Consulta o status do documento por ref."""
        params = {"completa": "1"} if completa else None
        return self._http.get(f"/v2/{self.endpoint}/{ref}", params=params)

    def cancelar(self, ref: str, justificativa: str) -> Dict[str, Any]:
        """Cancela o documento autorizado (mín. 15 caracteres na justificativa)."""
        if not justificativa or len(justificativa) < 15:
            raise ValueError("Justificativa de cancelamento exige no mínimo 15 caracteres.")
        return self._http.delete(
            f"/v2/{self.endpoint}/{ref}",
            json_body={"justificativa": justificativa},
        )

    # ----------------------------------------------------- arquivos
    def baixar_xml(self, ref: str) -> bytes:
        """Baixa o XML autorizado (binário)."""
        return self._http.get(f"/v2/{self.endpoint}/{ref}.xml", binary=True)

    def baixar_pdf(self, ref: str) -> bytes:
        """Baixa o DANFE/DACTE/etc. em PDF (binário)."""
        return self._http.get(f"/v2/{self.endpoint}/{ref}.pdf", binary=True)

    # ----------------------------------------------------- email
    def enviar_email(self, ref: str, emails: List[str]) -> Dict[str, Any]:
        """Reenvia o documento para uma lista de emails."""
        if not self.supports_email:
            raise NotImplementedError(f"{self.endpoint} não suporta envio de email.")
        return self._http.post(
            f"/v2/{self.endpoint}/{ref}/email",
            json_body={"emails": list(emails)},
        )

    # ----------------------------------------------------- inutilização
    def inutilizar(
        self,
        cnpj: str,
        serie: int,
        numero_inicial: int,
        numero_final: int,
        justificativa: str,
        ano: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Inutiliza uma faixa de numeração (NF-e, NFC-e). Mín. 15 chars na justificativa."""
        if not self.supports_inutilizacao:
            raise NotImplementedError(f"{self.endpoint} não suporta inutilização.")
        if not justificativa or len(justificativa) < 15:
            raise ValueError("Justificativa de inutilização exige no mínimo 15 caracteres.")
        body: Dict[str, Any] = {
            "cnpj": cnpj,
            "serie": serie,
            "numero_inicial": numero_inicial,
            "numero_final": numero_final,
            "justificativa": justificativa,
        }
        if ano is not None:
            body["ano"] = ano
        return self._http.post(f"/v2/{self.endpoint}_inutilizacao", json_body=body)

    # ----------------------------------------------------- carta de correção
    def carta_correcao(self, ref: str, correcao: str) -> Dict[str, Any]:
        """Envia carta de correção (se suportado pelo documento)."""
        if not self.supports_carta_correcao:
            raise NotImplementedError(f"{self.endpoint} não suporta carta de correção.")
        if not correcao or len(correcao) < 15:
            raise ValueError("Texto de correção exige no mínimo 15 caracteres.")
        return self._http.post(
            f"/v2/{self.endpoint}/{ref}/carta_correcao",
            json_body={"correcao": correcao},
        )
