"""
As telas da cadeia de frio: temperatura, posições e os alertas.

A TELA DE TEMPERATURA É DE QUEM MEDE, e quem mede está de pé na antecâmara
com um termômetro na mão. Por isso o registro fica no topo, com a câmara
já escolhida — e o histórico logo abaixo, para conferir se o número faz
sentido contra os últimos.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .forms_posicao import LeituraForm, PosicaoForm
from .models import Camara, LeituraTemperatura, LoteArmazenado, Posicao
from .services import FrioService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


class TemperaturaView(PolpaBaseView):
    """O painel das câmaras e o registro de uma leitura."""

    area = 'frio'

    def get(self, request):
        return render(request, 'polpa/temperatura.html', {
            'title': 'Registro de temperatura',
            'painel': FrioService.painel_temperatura(_filial(request)),
            'leituras': (
                LeituraTemperatura.objects.for_filial(_filial(request))
                .select_related('camara', 'medido_por')[:60]
            ),
            'form': LeituraForm(filial=_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'criar'),
        })

    def post(self, request):
        form = LeituraForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            messages.error(
                request,
                'Leitura não gravada: '
                + '; '.join(f'{c}: {e[0]}' for c, e in form.errors.items()),
            )
            return redirect(reverse('polpa:temperatura'))

        dados = form.cleaned_data
        try:
            leitura = FrioService.registrar_leitura(
                dados['camara'], dados['temperatura'], request.user,
                {
                    'medida_em': dados.get('medida_em'),
                    'observacao': dados.get('observacao') or '',
                },
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('polpa:temperatura'))

        if leitura.fora_da_faixa:
            # O DESVIO VOLTA NA TELA e vira notificação: quem mediu precisa
            # ver na hora, e quem não estava lá precisa ficar sabendo.
            messages.warning(
                request,
                f'{leitura.camara}: {leitura.temperatura}°C — '
                f'{leitura.desvio}°C fora da faixa. Um aviso foi disparado.',
            )
        else:
            messages.success(
                request, f'{leitura.camara}: {leitura.temperatura}°C registrados.',
            )
        return redirect(reverse('polpa:temperatura'))


class AlertasFrioView(PolpaBaseView):
    """Os quatro alertas, cada um com o que resolver."""

    area = 'frio'

    def get(self, request):
        return render(request, 'polpa/alertas_frio.html', {
            'title': 'Alertas da cadeia de frio',
            'alertas': FrioService.alertas(_filial(request)),
            'resumo': FrioService.resumo_alertas(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'editar'),
        })


class MapaCamaraView(PolpaBaseView):
    """As posições de uma câmara e o que está em cada uma."""

    area = 'frio'

    def get(self, request, pk):
        camara = get_object_or_404(
            Camara.objects.for_filial(_filial(request)), pk=pk,
        )
        return render(request, 'polpa/camara_mapa.html', {
            'title': f'Mapa — {camara.nome}',
            'camara': camara,
            'linhas': FrioService.mapa(camara),
            'leitura': FrioService.temperatura_atual(camara),
            'form': PosicaoForm(
                filial=_filial(request), initial={'camara': camara.pk},
            ),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'criar'),
        })


class PosicaoCreateView(PolpaBaseView):
    """Cadastra uma posição na câmara."""

    area = 'frio'
    permissao_acao = 'criar'

    def post(self, request, pk):
        camara = get_object_or_404(
            Camara.objects.for_filial(_filial(request)), pk=pk,
        )
        volta = redirect(reverse('polpa:camara-mapa', args=[camara.pk]))

        form = PosicaoForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            messages.error(
                request,
                'Posição não criada: '
                + '; '.join(
                    e[0] for e in form.errors.values()
                ),
            )
            return volta

        posicao = form.save()
        messages.success(request, f'Posição {posicao.codigo} criada.')
        return volta


class MoverLoteView(PolpaBaseView):
    """Move um lote de posição — e de câmara, quando for o caso."""

    area = 'frio'
    permissao_acao = 'editar'

    def post(self, request, pk):
        armazenado = get_object_or_404(
            LoteArmazenado.objects.for_filial(_filial(request))
            .select_related('lote', 'camara', 'posicao'),
            pk=pk,
        )
        volta = redirect(request.POST.get('voltar') or reverse('polpa:estoque-frio'))

        posicao = get_object_or_404(
            Posicao.objects.for_filial(_filial(request)).select_related('camara'),
            pk=request.POST.get('posicao'),
        )
        de = armazenado.onde or armazenado.camara.nome

        try:
            FrioService.mover(
                armazenado, posicao, request.user,
                (request.POST.get('motivo') or '').strip(),
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request,
            f'Lote {armazenado.lote.numero_lote}: {de} → '
            f'{posicao.camara.nome} {posicao.codigo}.',
        )
        return volta
