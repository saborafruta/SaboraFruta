from __future__ import annotations

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal
from apps.fiscal.integrations.focusnfe import FocusNFeClient
from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
from apps.fiscal.services.focusnfe_service import FocusNFeService


def obter_documento_fiscal(venda):
    """Localiza tambem documentos antigos que nao foram ligados na FK da venda."""
    if venda.documento_fiscal_id:
        return venda.documento_fiscal

    prioridade = Case(
        When(status=StatusDocumentoFiscal.AUTORIZADA, then=Value(0)),
        When(status=StatusDocumentoFiscal.PROCESSANDO, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    return (
        DocumentoFiscal.objects.filter(
            filial=venda.filial,
            origem_tipo="venda_pdv",
            origem_id=venda.pk,
            tipo_documento__in=("nfe", "nfce"),
        )
        .annotate(_prioridade=prioridade)
        .order_by("_prioridade", "-created_at")
        .first()
    )


def _focus_service_para_filial(filial) -> FocusNFeService:
    token = (getattr(filial, "focusnfe_token", "") or "").strip()
    ambiente = getattr(filial, "focusnfe_ambiente", None)
    if not token:
        return FocusNFeService()
    config = FocusNFeConfig.from_env(token=token, ambiente=ambiente)
    return FocusNFeService(client=FocusNFeClient(config=config))


@transaction.atomic
def cancelar_venda_e_documento(venda, usuario, justificativa: str):
    justificativa = (justificativa or "").strip()
    if len(justificativa) < 15:
        raise DadosInvalidosError(
            "Informe uma justificativa com ao menos 15 caracteres."
        )

    # Trave somente a venda. No PostgreSQL, combinar FOR UPDATE com
    # select_related em uma FK opcional tenta bloquear o lado anulavel do
    # OUTER JOIN e aborta o cancelamento antes mesmo da chamada a Focus.
    venda = type(venda).objects.select_for_update().get(pk=venda.pk)
    documento = obter_documento_fiscal(venda)

    if documento and documento.status == StatusDocumentoFiscal.AUTORIZADA:
        documento = _focus_service_para_filial(venda.filial).cancelar(
            documento, justificativa
        )
    elif documento and documento.status == StatusDocumentoFiscal.PROCESSANDO:
        raise DadosInvalidosError(
            "O documento fiscal ainda esta sendo processado. Consulte o status antes de cancelar."
        )

    if documento and venda.documento_fiscal_id != documento.pk:
        venda.documento_fiscal = documento

    venda.status = "cancelada"
    venda.motivo_cancelamento = justificativa
    venda.cancelado_em = timezone.now()
    venda.cancelado_por = usuario
    venda.save(update_fields=[
        "status",
        "motivo_cancelamento",
        "cancelado_em",
        "cancelado_por",
        "documento_fiscal",
    ])
    return documento
