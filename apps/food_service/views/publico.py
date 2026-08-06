"""
Cardápio Digital via QR Code -- ÚNICO arquivo do app sem PermissaoRequiredMixin.

Todo mundo aqui é alcançável por visitante anônimo. Nunca usar
`request.filial_ativa` (fica None pra usuário anônimo, ver
`apps.core.middleware.filial.FilialMiddleware`) -- a filial vem sempre de
`mesa.filial`, resolvida a partir do token da URL.
"""
import json

from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import ChamadoMesa, Comanda, Mesa
from apps.food_service.services import CardapioService, ChamadoService, PedidoPendenteService


def _mesa_do_token(token):
    return get_object_or_404(Mesa.all_objects.select_related('filial'), qr_token=token, ativo=True)


def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return {}


class CardapioView(View):
    def get(self, request, token):
        mesa = _mesa_do_token(token)
        filial = mesa.filial

        comanda_aberta = (
            Comanda.objects.for_filial(filial)
            .filter(mesas=mesa, status=Comanda.Status.ABERTA)
            .prefetch_related('itens__produto', 'itens__complementos__produto', 'pedidos_pendentes')
            .first()
        )
        comanda_para_avaliar = (
            Comanda.objects.for_filial(filial)
            .filter(mesas=mesa, status=Comanda.Status.FECHADA, avaliacao__isnull=True)
            .order_by('-fechada_em')
            .first()
        )

        comanda_itens = []
        if comanda_aberta:
            comanda_itens = [
                {
                    'nome': item.produto.descricao,
                    'quantidade': str(item.quantidade),
                    'valor_total': str(item.valor_total_com_complementos),
                }
                for item in comanda_aberta.itens.all()
            ]

        return render(request, 'food_service/publico/cardapio.html', {
            'mesa': mesa,
            'produtos': CardapioService.produtos_para_filial(filial),
            'mais_vendidos': CardapioService.mais_vendidos(filial),
            'promocoes': CardapioService.em_promocao(filial),
            'comanda_aberta': comanda_aberta,
            'comanda_itens': comanda_itens,
            'comanda_para_avaliar': comanda_para_avaliar,
        })


class PedidoPendenteCreateView(View):
    def post(self, request, token):
        from django.http import JsonResponse

        mesa = _mesa_do_token(token)
        body = _json_body(request)
        try:
            pedido = PedidoPendenteService.criar_pedido_pendente(mesa=mesa, itens=body.get('itens', []))
        except DadosInvalidosError as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        return JsonResponse({'ok': True, 'pedido_id': pedido.pk})


class ChamarGarcomView(View):
    def post(self, request, token):
        from django.http import JsonResponse

        mesa = _mesa_do_token(token)
        ChamadoService.criar_chamado(mesa=mesa, tipo=ChamadoMesa.Tipo.GARCOM)
        return JsonResponse({'ok': True})


class PedirContaView(View):
    def post(self, request, token):
        from django.http import JsonResponse

        mesa = _mesa_do_token(token)
        ChamadoService.criar_chamado(mesa=mesa, tipo=ChamadoMesa.Tipo.CONTA)
        return JsonResponse({'ok': True})


class AvaliacaoView(View):
    def get(self, request, token, comanda_id):
        mesa = _mesa_do_token(token)
        comanda = get_object_or_404(
            Comanda.objects.for_filial(mesa.filial).filter(mesas=mesa),
            pk=comanda_id, status=Comanda.Status.FECHADA,
        )
        ja_avaliada = hasattr(comanda, 'avaliacao')
        return render(request, 'food_service/publico/avaliacao.html', {
            'mesa': mesa,
            'comanda': comanda,
            'ja_avaliada': ja_avaliada,
        })

    def post(self, request, token, comanda_id):
        from apps.food_service.models import AvaliacaoAtendimento

        mesa = _mesa_do_token(token)
        comanda = get_object_or_404(
            Comanda.objects.for_filial(mesa.filial).filter(mesas=mesa),
            pk=comanda_id, status=Comanda.Status.FECHADA,
        )
        if hasattr(comanda, 'avaliacao'):
            return render(request, 'food_service/publico/avaliacao.html', {
                'mesa': mesa, 'comanda': comanda, 'ja_avaliada': True,
            })

        try:
            nota = int(request.POST.get('nota', '0'))
        except ValueError:
            nota = 0
        if nota < 1 or nota > 5:
            return render(request, 'food_service/publico/avaliacao.html', {
                'mesa': mesa, 'comanda': comanda, 'ja_avaliada': False,
                'erro': 'Escolha uma nota de 1 a 5.',
            })

        AvaliacaoAtendimento.objects.create(
            comanda=comanda,
            nota=nota,
            comentario=(request.POST.get('comentario') or '').strip()[:1000],
        )
        return render(request, 'food_service/publico/avaliacao.html', {
            'mesa': mesa, 'comanda': comanda, 'ja_avaliada': True, 'agradecer': True,
        })
