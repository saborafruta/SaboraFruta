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

    # ----------------------------------------------------- DAMDFE
    #: Chaves onde a consulta pode trazer o caminho do PDF, em ordem de
    #: preferencia. A Focus usa `caminho_damdfe`; as outras cobrem variacoes
    #: entre ambientes sem exigir uma alteracao de codigo.
    CHAVES_PDF = ("caminho_damdfe", "caminho_pdf", "caminho_danfe")

    def baixar_pdf(self, ref: str) -> bytes:
        """
        Baixa o DAMDFE.

        Diferente da NF-e, o endpoint `/v2/mdfe/{ref}.pdf` **nao** devolve o
        arquivo: a Focus ignora o sufixo e responde o JSON de consulta, com
        status 200. Servir esses bytes como PDF produzia um "Falha ao carregar
        documento" no navegador, sem pista do motivo.

        O caminho correto e consultar e seguir o link que vem na resposta.
        """
        dados = self.consultar(ref)
        url = self._url_do_pdf(dados)
        if not url:
            from ..exceptions import FocusNFeError

            status = (dados or {}).get("status", "?") if isinstance(dados, dict) else "?"
            raise FocusNFeError(
                "A consulta do MDF-e nao trouxe o caminho do DAMDFE "
                f"(status: {status}). Se ele acabou de ser autorizado, "
                "aguarde alguns segundos e tente de novo."
            )
        return self._http.get(url, binary=True)

    @classmethod
    def _url_do_pdf(cls, dados) -> str:
        """Extrai o link do DAMDFE da resposta de consulta."""
        if not isinstance(dados, dict):
            return ""
        for chave in cls.CHAVES_PDF:
            valor = str(dados.get(chave) or "").strip()
            if valor:
                return valor
        # Ultimo recurso: qualquer `caminho_*` que aponte para um .pdf. Cobre
        # um nome de campo novo sem quebrar o download.
        for chave, valor in dados.items():
            if (
                str(chave).startswith("caminho_")
                and str(valor or "").lower().endswith(".pdf")
            ):
                return str(valor).strip()
        return ""

    def baixar_damdfe(self, ref: str) -> bytes:
        return self.baixar_pdf(ref)
