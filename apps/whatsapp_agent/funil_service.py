"""Integra as conversas centrais ao funil comercial do banco operacional."""

import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch, Q

from apps.cadastros.models import Cliente
from apps.core.models import Filial, Usuario
from apps.core.tenant_context import get_current_database_alias, tenant_db
from apps.crm.services.comercial_service import ComercialService, EtapaAutomaticaCRM


logger = logging.getLogger(__name__)


def sincronizar_funil_whatsapp(
    conversa, etapa_nome, *, filial=None, cliente=None, responsavel=None,
    pedido=None, motivo_perda='', autor=None,
):
    """Cria ou move o único cartão comercial vinculado à conversa."""
    alias = conversa.tenant_alias or get_current_database_alias()
    cliente_id = getattr(cliente, 'pk', None) or conversa.cliente_operacional_id
    if not alias or not cliente_id:
        return None

    conexao = getattr(conversa, 'conexao_vendedor', None)
    vendedor_email = (
        getattr(getattr(conexao, 'vendedor', None), 'email', '') if conexao else ''
    )
    empresa_cnpj = (
        getattr(getattr(conexao, 'empresa', None), 'cnpj', '') if conexao else ''
    )
    filial_id = getattr(filial, 'pk', None) or getattr(pedido, 'filial_id', None)

    try:
        with tenant_db(alias):
            cliente = cliente or Cliente.objects.using(alias).select_related(
                'filial', 'filial__empresa',
            ).get(pk=cliente_id)
            filial = filial or getattr(pedido, 'filial', None)
            if filial is None and filial_id:
                filial = Filial.objects.using(alias).get(pk=filial_id)
            filial = filial or cliente.filial
            responsavel = responsavel or getattr(pedido, 'usuario', None)
            if responsavel is None and vendedor_email:
                responsavel = Usuario.objects.using(alias).filter(
                    email__iexact=vendedor_email,
                    empresa__cnpj=empresa_cnpj,
                    ativo=True,
                ).first()
            if responsavel is None:
                return None
            return ComercialService.sincronizar_conversa_whatsapp(
                conversa_id=conversa.pk,
                filial=filial,
                cliente=cliente,
                responsavel=responsavel,
                etapa_nome=etapa_nome,
                valor_estimado=(pedido.valor_total if pedido is not None else None),
                motivo_perda=motivo_perda,
                autor=autor,
            )
    except (Cliente.DoesNotExist, Filial.DoesNotExist):
        logger.warning(
            'Conversa do WhatsApp sem cliente ou filial operacional para o funil',
            extra={'conversa_id': conversa.pk, 'tenant_alias': alias},
        )
        return None


def reconciliar_funil_whatsapp(*, filial, usuario, pode_supervisionar=False):
    """Traz conversas e orçamentos anteriores para o funil automático."""
    from .models import ConversaWhatsApp, OrcamentoWhatsApp
    from .orcamento_service import pedido_do_orcamento

    alias = get_current_database_alias()
    orcamentos = OrcamentoWhatsApp.objects.using('default').order_by('-updated_at', '-pk')
    conversas = (
        ConversaWhatsApp.objects.using('default')
        .filter(
            ativa=True,
            cliente_operacional_id__isnull=False,
            conexao_vendedor__empresa__cnpj=filial.empresa.cnpj,
        )
        .filter(Q(tenant_alias=alias) | Q(tenant_alias=''))
        .select_related(
            'conexao_vendedor', 'conexao_vendedor__vendedor',
            'conexao_vendedor__empresa',
        )
        .prefetch_related(Prefetch('orcamentos', queryset=orcamentos, to_attr='orcamentos_funil'))
        .order_by('-ultima_mensagem_em', '-updated_at')[:500]
    )
    if not pode_supervisionar:
        conversas = [
            conversa for conversa in conversas
            if conversa.conexao_vendedor.vendedor.email.lower() == usuario.email.lower()
        ]

    mapa_etapas = {
        OrcamentoWhatsApp.Status.RASCUNHO: EtapaAutomaticaCRM.ORCANDO,
        OrcamentoWhatsApp.Status.ENVIADO: EtapaAutomaticaCRM.AGUARDANDO_APROVACAO,
        OrcamentoWhatsApp.Status.APROVADO: EtapaAutomaticaCRM.APROVADOS,
        OrcamentoWhatsApp.Status.RECUSADO: EtapaAutomaticaCRM.RECUSADOS,
    }
    for conversa in conversas:
        orcamento = conversa.orcamentos_funil[0] if conversa.orcamentos_funil else None
        pedido = None
        etapa = EtapaAutomaticaCRM.CONVERSAS
        motivo = ''
        if orcamento:
            try:
                pedido = pedido_do_orcamento(orcamento)
            except ObjectDoesNotExist:  # um legado inconsistente não derruba o funil inteiro
                logger.exception(
                    'Orçamento sem pedido operacional durante reconciliação do funil',
                    extra={'orcamento_id': orcamento.pk, 'conversa_id': conversa.pk},
                )
                continue
            etapa = mapa_etapas[orcamento.status]
            motivo = orcamento.motivo_recusa
        sincronizar_funil_whatsapp(
            conversa,
            etapa,
            filial=(pedido.filial if pedido is not None else filial),
            pedido=pedido,
            motivo_perda=motivo,
        )
