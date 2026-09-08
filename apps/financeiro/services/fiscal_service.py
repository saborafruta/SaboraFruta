"""Serviços fiscais — emissão de NF-e/NFC-e via Focus NFe (esqueleto)."""
import hashlib
import logging
from datetime import datetime, timedelta
from django.utils import timezone
from django.conf import settings
from apps.core.tenant_context import tenant_atomic
from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.models import (
    DocumentoFiscal, IdempotenciaFiscal, LogIntegracaoFiscal,
)

logger = logging.getLogger("erp.fiscal")


class FiscalService:
    """Camada de emissão fiscal. Wrapper sobre Focus NFe / SEFAZ direto."""

    @staticmethod
    def _ambiente_emissao(documento_fiscal: DocumentoFiscal) -> int:
        return int(
            getattr(documento_fiscal.filial, 'focusnfe_ambiente', None)
            or getattr(settings, 'ERP_FOCUSNFE_AMBIENTE', 2)
            or 2
        )

    @staticmethod
    def validar_emissao_segura(documento_fiscal: DocumentoFiscal) -> None:
        ambiente = FiscalService._ambiente_emissao(documento_fiscal)
        if ambiente == 1 and not getattr(settings, 'FISCAL_ALLOW_PRODUCTION_EMISSION', False):
            raise DadosInvalidosError(
                'Emissao fiscal em producao bloqueada por seguranca. '
                'Use homologacao ou libere explicitamente FISCAL_ALLOW_PRODUCTION_EMISSION.'
            )

    @staticmethod
    def gerar_chave_idempotencia(origem_tipo, origem_id, filial_id, tipo_doc):
        raw = f"{origem_tipo}:{origem_id}:{filial_id}:{tipo_doc}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    @tenant_atomic
    def reservar_idempotencia(filial, chave, tipo_doc):
        idemp, created = IdempotenciaFiscal.objects.select_for_update().get_or_create(
            filial=filial, chave=chave,
            defaults={
                "tipo_documento": tipo_doc, "status": "processando",
                "expires_at": timezone.now() + timedelta(hours=24),
            },
        )
        if not created and idemp.status == "sucesso":
            return idemp, False
        return idemp, True

    @staticmethod
    def emitir_nfe(documento_fiscal: DocumentoFiscal):
        """Emissão de NF-e — chamada simplificada ao Focus NFe."""
        FiscalService.validar_emissao_segura(documento_fiscal)
        # Implementação real: chamar API Focus NFe e processar XML/protocolo.
        # Aqui registramos só um log para demonstrar o fluxo.
        LogIntegracaoFiscal.objects.create(
            filial=documento_fiscal.filial,
            documento_fiscal=documento_fiscal,
            provedor="focusnfe",
            acao="emissao",
            endpoint=f"/v2/nfe?ref={documento_fiscal.id}",
            sucesso=False,
            tempo_resposta_ms=0,
        )
        # TODO: integração real
        return documento_fiscal


def limpar_idempotencia_expirada():
    """Job noturno — remove registros de idempotência expirados."""
    n = IdempotenciaFiscal.objects.filter(expires_at__lt=timezone.now()).delete()
    return n
