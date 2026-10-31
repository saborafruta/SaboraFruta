from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from apps.core.tenant_context import get_current_database_alias

from .models import OrcamentoWhatsApp
from .orcamento_service import pedido_do_orcamento


def _privado(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _buscar(token, *, bloquear=False):
    if len(token) != 43:
        raise Http404
    queryset = OrcamentoWhatsApp.objects.using('default')
    if bloquear:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, token_publico=token)


class OrcamentoPublicoView(View):
    template_name = 'whatsapp_agent/orcamento_publico.html'

    def get(self, request, token):
        return self._render(request, _buscar(token))

    def post(self, request, token):
        alias = get_current_database_alias()
        with transaction.atomic(using=alias), transaction.atomic(using='default'):
            orcamento = _buscar(token, bloquear=True)
            if orcamento.tenant_alias != alias:
                raise Http404
            if orcamento.status != OrcamentoWhatsApp.Status.ENVIADO:
                return self._render(request, orcamento)
            pedido = pedido_do_orcamento(orcamento, bloquear=True)
            if pedido.data_validade_orcamento and pedido.data_validade_orcamento < timezone.localdate():
                return self._render(request, orcamento, erro='Este orçamento passou da validade.')

            acao = request.POST.get('acao')
            if acao == 'aprovar':
                orcamento.status = OrcamentoWhatsApp.Status.APROVADO
                orcamento.respondido_em = timezone.now()
                orcamento.motivo_recusa = ''
                pedido.tipo = pedido.Tipo.PEDIDO
                pedido.status = pedido.Status.APROVADO
                pedido.data_aprovacao = timezone.now()
                pedido.observacao_interna = (
                    (pedido.observacao_interna + '\n') if pedido.observacao_interna else ''
                ) + 'Aprovado pelo cliente no link enviado pelo CRM do WhatsApp.'
                pedido.save(update_fields=[
                    'tipo', 'status', 'data_aprovacao', 'observacao_interna', 'updated_at',
                ])
                orcamento.save(update_fields=[
                    'status', 'respondido_em', 'motivo_recusa', 'updated_at',
                ])
                return self._render(request, orcamento, sucesso='Orçamento aprovado com sucesso.')

            if acao == 'recusar':
                motivo = (request.POST.get('motivo') or '').strip()
                if len(motivo) < 3:
                    return self._render(
                        request, orcamento,
                        erro='Conte brevemente por que o orçamento não foi aprovado.',
                    )
                orcamento.status = OrcamentoWhatsApp.Status.RECUSADO
                orcamento.respondido_em = timezone.now()
                orcamento.motivo_recusa = motivo[:2000]
                pedido.status = pedido.Status.CANCELADO
                pedido.motivo_cancelamento = f'Orçamento não aprovado pelo cliente: {motivo}'[:5000]
                pedido.save(update_fields=['status', 'motivo_cancelamento', 'updated_at'])
                orcamento.save(update_fields=[
                    'status', 'respondido_em', 'motivo_recusa', 'updated_at',
                ])
                return self._render(
                    request, orcamento,
                    sucesso='Resposta registrada. O vendedor verá o motivo no CRM.',
                )
            raise Http404

    def _render(self, request, orcamento, **extra):
        if orcamento.tenant_alias != get_current_database_alias():
            raise Http404
        contexto = {
            'orcamento': orcamento,
            'pedido': pedido_do_orcamento(orcamento),
            **extra,
        }
        return _privado(render(request, self.template_name, contexto))
