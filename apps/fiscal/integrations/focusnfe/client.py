"""
Facade FocusNFeClient — ponto único de entrada para as APIs Focus NFe.

Uso:
    from apps.fiscal.integrations.focusnfe import FocusNFeClient

    client = FocusNFeClient()                        # config via env/settings
    client.nfe.autorizar("PED-001", payload_dict)
    client.cnpjs.consultar("00000000000191")
    client.consultas.ncm.consultar("33049910")       # também atalho
"""
from __future__ import annotations

from typing import Optional

from ._base import BaseAPIClient
from .config import FocusNFeConfig

# Resources
from .resources.nfe import NFeResource
from .resources.nfce import NFCeResource
from .resources.nfse import NFSeResource
from .resources.nfse_nacional import NFSeNacionalResource
from .resources.nfse_arquivo import NFSeArquivoResource
from .resources.nfse_recebidas import NFSeRecebidasResource
from .resources.cte import CTeResource
from .resources.cte_os import CTeOSResource
from .resources.nfcom import NFCOMResource
from .resources.mdfe import MDFeResource
from .resources.nfe_recebidas import NFeRecebidasResource
from .resources.cte_recebidas import CTeRecebidasResource
from .resources.backups import BackupsResource
from .resources.consultas import (
    NCMResource,
    CFOPResource,
    CNAEResource,
    CNPJResource,
    MunicipiosResource,
)
from .resources.empresas import EmpresasResource


class _ConsultasNamespace:
    """Agrupa as APIs de consulta auxiliar para uso como `client.consultas.ncm`."""

    def __init__(self, http: BaseAPIClient) -> None:
        self.ncm = NCMResource(http)
        self.cfop = CFOPResource(http)
        self.cnae = CNAEResource(http)
        self.cnpj = CNPJResource(http)
        self.municipios = MunicipiosResource(http)


class FocusNFeClient:
    """
    Cliente principal. Cada atributo é um resource:

      Documentos com autorização SEFAZ:
        client.nfe         (NFe)
        client.nfce        (NFCe)
        client.cte         (CTe)
        client.cte_os      (CTe OS)
        client.nfcom       (NFCOM)
        client.mdfe        (MDFe)

      NFSe (serviço):
        client.nfse                (municipal)
        client.nfse_nacional       (padrão nacional Receita Federal)
        client.nfse_arquivo        (envio por arquivo)
        client.nfse_recebidas      (NFSes recebidas pelo CNPJ tomador)

      Documentos recebidos:
        client.nfe_recebidas
        client.cte_recebidas

      Consultas auxiliares:
        client.ncms        | client.consultas.ncm
        client.cfops       | client.consultas.cfop
        client.cnaes       | client.consultas.cnae
        client.cnpjs       | client.consultas.cnpj
    """

    def __init__(
        self,
        config: Optional[FocusNFeConfig] = None,
        token: Optional[str] = None,
        ambiente: Optional[int] = None,
    ) -> None:
        if config is None:
            config = FocusNFeConfig.from_env(token=token, ambiente=ambiente)
        self.config = config
        self.http = BaseAPIClient(config=config)

        # Documentos autorizados
        self.nfe = NFeResource(self.http)
        self.nfce = NFCeResource(self.http)
        self.cte = CTeResource(self.http)
        self.cte_os = CTeOSResource(self.http)
        self.nfcom = NFCOMResource(self.http)
        self.mdfe = MDFeResource(self.http)

        # NFSe (família)
        self.nfse = NFSeResource(self.http)
        self.nfse_nacional = NFSeNacionalResource(self.http)
        self.nfse_arquivo = NFSeArquivoResource(self.http)
        self.nfse_recebidas = NFSeRecebidasResource(self.http)

        # Recebidos
        self.nfe_recebidas = NFeRecebidasResource(self.http)
        self.cte_recebidas = CTeRecebidasResource(self.http)

        # Consultas
        self.ncms = NCMResource(self.http)
        self.cfops = CFOPResource(self.http)
        self.cnaes = CNAEResource(self.http)
        self.cnpjs = CNPJResource(self.http)
        self.municipios = MunicipiosResource(self.http)

        # Gestão de empresas Focus (certificado, CSC, regime, etc.)
        self.empresas = EmpresasResource(self.http)
        self.backups = BackupsResource(self.http)

        # Namespace agrupado
        self.consultas = _ConsultasNamespace(self.http)

    @property
    def ambiente_label(self) -> str:
        return "produção" if self.config.ambiente == 1 else "homologação"

    def __repr__(self) -> str:
        return f"<FocusNFeClient ambiente={self.ambiente_label}>"
