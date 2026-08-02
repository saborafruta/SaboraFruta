"""
Cercas virtuais: cadastro, ingestão de posição e eventos (§12).

A ingestão fica aqui e não num app à parte porque é o mínimo do §13 que o §12
exige para existir: sem alguém dizendo onde o motorista está, a cerca nunca
dispara. O restante do rastreamento (mapa ao vivo, trilha do percurso) segue
fora de escopo.
"""
from __future__ import annotations

import datetime
import json
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from apps.core.services.permissions import PermissaoRequiredMixin, requer_permissao
from apps.mapas.models import Geofence
from apps.mapas.services.geofence import GeofenceService

logger = logging.getLogger(__name__)


def _data(valor):
    try:
        return datetime.date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


class GeofenceListView(PermissaoRequiredMixin, ListView):
    template_name = 'mapas/geofence_list.html'
    context_object_name = 'cercas'
    permissao_modulo = 'mapas'
    permissao_acao = 'ver'
    paginate_by = 50

    def get_queryset(self):
        return (
            Geofence.objects
            .filter(filial__in=GeofenceService._escopo(
                getattr(self.request, 'filial_ativa', None)))
            .select_related('cliente')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Cercas Virtuais'
        ctx['pode_editar'] = self.request.user.tem_permissao('mapas', 'editar')
        return ctx


class GeofenceFormMixin(PermissaoRequiredMixin):
    model = Geofence
    template_name = 'mapas/geofence_form.html'
    fields = ['nome', 'latitude', 'longitude', 'raio_m', 'cliente',
              'ativo', 'observacao']
    success_url = reverse_lazy('mapas:geofence-list')
    permissao_modulo = 'mapas'
    permissao_acao = 'editar'

    def get_form(self, *args, **kwargs):
        """
        Escopa o select de cliente à filial ativa.

        Um ModelForm por `fields` monta o select com **todos** os clientes de
        todas as empresas — num SaaS isso é vazamento, mesmo que o usuário só
        veja nomes.
        """
        form = super().get_form(*args, **kwargs)
        from apps.cadastros.models import Cliente

        filiais = GeofenceService._escopo(
            getattr(self.request, 'filial_ativa', None))
        form.fields['cliente'].queryset = Cliente.objects.filter(
            filial__in=filiais, latitude__isnull=False,
        ).order_by('razao_social')
        form.fields['cliente'].required = False
        return form

    def form_valid(self, form):
        form.instance.filial = getattr(self.request, 'filial_ativa', None)
        messages.success(self.request, 'Cerca salva.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Cerca Virtual'
        return ctx


class GeofenceCreateView(GeofenceFormMixin, CreateView):
    pass


class GeofenceUpdateView(GeofenceFormMixin, UpdateView):
    def get_queryset(self):
        return Geofence.objects.filter(filial__in=GeofenceService._escopo(
            getattr(self.request, 'filial_ativa', None)))


class GeofenceDeleteView(PermissaoRequiredMixin, DeleteView):
    model = Geofence
    template_name = 'mapas/geofence_confirm_delete.html'
    success_url = reverse_lazy('mapas:geofence-list')
    permissao_modulo = 'mapas'
    permissao_acao = 'excluir'

    def get_queryset(self):
        return Geofence.objects.filter(filial__in=GeofenceService._escopo(
            getattr(self.request, 'filial_ativa', None)))


class GeofenceEventosView(PermissaoRequiredMixin, TemplateView):
    """Visitas registradas: entrada, saída e permanência."""

    template_name = 'mapas/geofence_eventos.html'
    permissao_modulo = 'mapas'
    permissao_acao = 'ver'

    def get_context_data(self, **kwargs):
        from apps.cadastros.models import Motorista

        ctx = super().get_context_data(**kwargs)
        filial = getattr(self.request, 'filial_ativa', None)
        filiais = GeofenceService._escopo(filial)

        ctx['title'] = 'Eventos de Cerca'
        ctx['dados'] = GeofenceService.visitas(
            filial,
            inicio=_data(self.request.GET.get('de')),
            fim=_data(self.request.GET.get('ate')),
            geofence_id=_inteiro(self.request.GET.get('cerca')),
            motorista_id=_inteiro(self.request.GET.get('motorista')),
        )
        ctx['cercas'] = Geofence.objects.filter(filial__in=filiais)
        ctx['motoristas'] = Motorista.objects.filter(filial__in=filiais).order_by('nome')
        ctx['cerca_sel'] = self.request.GET.get('cerca', '')
        ctx['motorista_sel'] = self.request.GET.get('motorista', '')
        return ctx


# ─────────────────────────────────────────────── ingestão de posição
@require_POST
@requer_permissao('mapas', 'ver')
def registrar_posicao(request):
    """
    POST /mapas/api/posicao/  {"motorista": 3, "lat": -5.79, "lng": -35.21}

    Recebe uma posição e devolve os eventos que ela provocou — quase sempre
    nenhum, porque só há evento quando o motorista cruza uma cerca.

    **A posição não é armazenada.** Só as travessias ficam. Guardar cada ping
    seriam milhares de linhas por dia por motorista para responder o que dois
    registros por visita já respondem; quando o §13 (trilha do percurso) for
    feito, aí sim entra uma tabela própria, com política de retenção.
    """
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)

    from apps.cadastros.models import Motorista
    from apps.mapas import constants as c

    try:
        lat = float(corpo.get('lat'))
        lng = float(corpo.get('lng'))
    except (TypeError, ValueError):
        return JsonResponse({'erro': 'Informe lat e lng.'}, status=400)

    if not c.dentro_do_brasil(lat, lng):
        return JsonResponse({'erro': 'Coordenada fora do Brasil.'}, status=400)

    filial = getattr(request, 'filial_ativa', None)
    motorista = Motorista.objects.filter(
        pk=_inteiro(corpo.get('motorista')),
        filial__in=GeofenceService._escopo(filial),
    ).first()
    if motorista is None:
        return JsonResponse({'erro': 'Motorista não encontrado.'}, status=404)

    eventos = GeofenceService.processar_posicao(
        filial=filial, motorista=motorista, latitude=lat, longitude=lng,
    )
    return JsonResponse({
        'ok': True,
        'eventos': [
            {
                'cerca': e.geofence.nome,
                'tipo': e.tipo,
                'momento': timezone.localtime(e.momento).isoformat(),
                'distancia_m': e.distancia_m,
            }
            for e in eventos
        ],
    })


@requer_permissao('mapas', 'ver')
def pagina_rastreio(request):
    """
    Página que o motorista deixa aberta no celular.

    Usa a geolocalização do próprio navegador e manda a posição de tempos em
    tempos. É a fonte de posição mais simples que faz o §12 funcionar hoje,
    sem depender de comprar rastreador ou publicar aplicativo.

    Duas limitações que a própria tela avisa, porque descobrir isso em campo
    seria pior: a página precisa ficar aberta (navegador em segundo plano
    corta a atualização) e o site tem de estar em HTTPS, senão o navegador
    recusa dar a localização.
    """
    from apps.cadastros.models import Motorista

    filiais = GeofenceService._escopo(getattr(request, 'filial_ativa', None))
    return render(request, 'mapas/rastreio.html', {
        'title': 'Rastreio do Motorista',
        'motoristas': Motorista.objects.filter(
            filial__in=filiais, ativo=True).order_by('nome'),
    })
