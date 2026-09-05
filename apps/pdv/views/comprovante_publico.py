"""Comprovante público por URL-capacidade, como o pedido da OP.

Não expõe IDs sequenciais, listagens, CPF, telefone ou dados internos da venda.
O link só nasce por ação autenticada na filial proprietária. Cancelamento
invalida o documento público, assim como a retomada da venda para edição.
"""
import secrets

from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core.services.permissions import requer_permissao
from apps.pdv.models import VendaPDV
from apps.pdv.services.comprovante_service import dados_comprovante, gerar_pdf


def privado(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@requer_permissao('pdv', 'ver')
@require_POST
def criar_link(request, pk):
    with transaction.atomic():
        venda = get_object_or_404(
            VendaPDV.objects.for_filial(request.filial_ativa).select_for_update(),
            pk=pk, status='finalizada',
        )
        if not venda.comprovante_token:
            venda.comprovante_token = secrets.token_urlsafe(32)
            venda.save(update_fields=['comprovante_token'])
    url = request.build_absolute_uri(reverse('pdv_publico:comprovante', args=[venda.comprovante_token]))
    return privado(JsonResponse({'ok': True, 'url': url}))


def buscar(token):
    if len(token) != 43:
        raise Http404
    return get_object_or_404(
        VendaPDV.objects.select_related('filial__empresa', 'cliente')
        .prefetch_related('itens__produto', 'pagamentos__forma_pagamento'),
        comprovante_token=token, status='finalizada',
    )


def buscar_interno(request, pk):
    return get_object_or_404(
        VendaPDV.objects.for_filial(request.filial_ativa)
        .select_related('filial__empresa', 'cliente')
        .prefetch_related('itens__produto', 'pagamentos__forma_pagamento'),
        pk=pk, status='finalizada',
    )


@requer_permissao('pdv', 'ver')
@require_GET
def visualizar_interno(request, pk):
    venda = buscar_interno(request, pk)
    return privado(render(request, 'pdv/comprovante_publico.html', {
        'cupom': dados_comprovante(venda),
        'pdf_url': reverse('pdv:comprovante_venda_pdf', args=[venda.pk]),
    }))


@requer_permissao('pdv', 'ver')
@require_GET
def baixar_pdf_interno(request, pk):
    venda = buscar_interno(request, pk)
    response = HttpResponse(gerar_pdf(venda), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="comprovante-{venda.numero_venda:06d}.pdf"'
    return privado(response)


@require_GET
def visualizar(request, token):
    venda = buscar(token)
    return privado(render(request, 'pdv/comprovante_publico.html', {
        'cupom': dados_comprovante(venda),
        'pdf_url': reverse('pdv_publico:pdf', args=[token]),
    }))


@require_GET
def baixar_pdf(request, token):
    venda = buscar(token)
    response = HttpResponse(gerar_pdf(venda), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="comprovante-{venda.numero_venda:06d}.pdf"'
    return privado(response)
