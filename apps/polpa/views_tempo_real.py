"""
O painel de chão de fábrica: o dia, agora.

É TELA DE PAREDE. Fica aberta num monitor da produção, e por isso se
atualiza sozinha e mostra número grande — quem lê está a três metros de
distância, de pé, com luva.

TEM OUTRO DONO que o painel industrial: aquele é de quem decide (30 dias,
custo, tendência); este é de quem está produzindo agora, e a pergunta é uma
só — "estamos no ritmo?".
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms_meta import MetaForm
from .models import MetaProducao
from .services import TempoRealService
from .views import PolpaBaseView

# De quanto em quanto tempo a tela se recarrega sozinha, em segundos. Um
# minuto é o intervalo em que a produção muda de verdade — recarregar a cada
# cinco segundos só daria piscada e consulta.
SEGUNDOS_ATUALIZACAO = 60


def _filial(request):
    return request.filial_ativa


class TempoRealView(PolpaBaseView):
    """Produção de hoje, contra a meta do dia."""

    area = 'indicadores'

    def get(self, request):
        return render(request, 'polpa/tempo_real.html', {
            'title': 'Produção de hoje',
            'dados': TempoRealService.hoje(_filial(request)),
            'segundos': SEGUNDOS_ATUALIZACAO,
            'pode_agir': request.user.tem_permissao('polpa_indicadores', 'criar'),
        })


class MetaListView(PolpaBaseView):
    """As metas: a padrão e as dos dias específicos."""

    area = 'indicadores'

    def get(self, request):
        return self._tela(request, MetaForm(filial=_filial(request)))

    def post(self, request):
        form = MetaForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            return self._tela(request, form)

        meta = form.save()
        messages.success(request, f'{meta} gravada.')
        return redirect(reverse('polpa:meta-list'))

    @staticmethod
    def _tela(request, form):
        metas = MetaProducao.objects.for_filial(_filial(request))
        return render(request, 'polpa/meta_list.html', {
            'title': 'Metas de produção',
            'padrao': metas.filter(data__isnull=True).first(),
            'metas': metas.filter(data__isnull=False)[:60],
            'form': form,
            'pode_agir': request.user.tem_permissao('polpa_indicadores', 'criar'),
        })


class MetaUpdateView(PolpaBaseView):
    """Corrige uma meta existente."""

    area = 'indicadores'
    permissao_acao = 'criar'

    def post(self, request, pk):
        meta = get_object_or_404(
            MetaProducao.objects.for_filial(_filial(request)), pk=pk,
        )
        form = MetaForm(request.POST, instance=meta, filial=_filial(request))
        if not form.is_valid():
            messages.error(
                request,
                'Meta não gravada: '
                + '; '.join(e[0] for e in form.errors.values()),
            )
        else:
            form.save()
            messages.success(request, 'Meta atualizada.')
        return redirect(reverse('polpa:meta-list'))
