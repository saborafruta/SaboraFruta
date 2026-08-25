"""
As telas do estoque congelado: os lotes, onde estão e quando vencem.

A PERGUNTA DESTA TELA É "ONDE ESTÁ E QUANTO FALTA PARA VENCER". Quanto tem
o ERP já responde; o que faltava era a câmara, o endereço e o prazo — e é
com essas três que se separa um pedido às 4h da manhã sem deixar a porta da
câmara aberta procurando.
"""
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto

from .forms_camara import CamaraForm
from .models import Camara, FichaProduto
from .services import ArmazenagemService
from .views import PolpaBaseView

SITUACOES = (
    ('vencido', 'Vencidos'),
    ('vencendo', 'Vencendo'),
    ('ok', 'No prazo'),
    ('sem_validade', 'Sem validade'),
)


def _filial(request):
    return request.filial_ativa


class EstoqueFrioView(PolpaBaseView):
    """Os lotes de produto acabado, em ordem FEFO."""

    area = 'frio'

    def get(self, request):
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'camara': (request.GET.get('camara') or '').strip(),
            'situacao': (request.GET.get('situacao') or '').strip(),
            'sem_endereco': request.GET.get('sem_endereco') == '1',
        }
        linhas = ArmazenagemService.estoque(_filial(request), filtros)

        return render(request, 'polpa/estoque_frio.html', {
            'title': 'Estoque congelado',
            'linhas': linhas[:400],
            'filtros': filtros,
            'tem_filtro': any(filtros.values()),
            'situacoes': SITUACOES,
            'camaras': Camara.objects.for_filial(_filial(request)).filter(ativo=True),
            'resumo': ArmazenagemService.resumo(_filial(request)),
            'por_camara': ArmazenagemService.por_camara(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'editar'),
        })


class GuardarLoteView(PolpaBaseView):
    """Põe (ou muda) o lote numa câmara e num endereço."""

    area = 'frio'
    permissao_acao = 'editar'

    def post(self, request, pk):
        lote = get_object_or_404(
            LoteProduto.objects.filter(filial=_filial(request)), pk=pk,
        )
        volta = redirect(request.POST.get('voltar') or reverse('polpa:estoque-frio'))

        camara = get_object_or_404(
            Camara.objects.for_filial(_filial(request)),
            pk=request.POST.get('camara'),
        )
        temperatura = (request.POST.get('temperatura_entrada') or '').strip()
        try:
            temperatura = Decimal(temperatura.replace(',', '.')) if temperatura else None
        except (InvalidOperation, ValueError):
            temperatura = None

        try:
            armazenado = ArmazenagemService.guardar(lote, camara, {
                'endereco': request.POST.get('endereco') or '',
                'temperatura_entrada': temperatura,
            })
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        if armazenado.fora_da_faixa:
            # A CÂMARA NÃO ALCANÇA a temperatura que o produto exige. Não
            # trava — quem está com o pallet na mão pode não ter outra
            # câmara agora — mas ninguém pode dizer depois que não sabia.
            messages.warning(
                request,
                f'Lote {lote.numero_lote} guardado em {camara}, mas a câmara '
                f'não alcança a temperatura que {lote.produto} exige.',
            )
        else:
            messages.success(
                request,
                f'Lote {lote.numero_lote} guardado em {camara}'
                + (f', {armazenado.endereco}.' if armazenado.endereco else '.'),
            )
        return volta


class BloquearLoteView(PolpaBaseView):
    """Tira o lote do jogo, sem apagá-lo."""

    area = 'frio'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        lote = get_object_or_404(
            LoteProduto.objects.filter(filial=_filial(request)), pk=pk,
        )
        try:
            ArmazenagemService.bloquear(lote, request.POST.get('motivo') or '')
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(
                request, f'Lote {lote.numero_lote} bloqueado — sai da separação.',
            )
        return redirect(request.POST.get('voltar') or reverse('polpa:estoque-frio'))


class ValidadeView(PolpaBaseView):
    """O que está para vencer — a fila do que decidir."""

    area = 'frio'

    def get(self, request):
        try:
            dias = int(request.GET.get('dias') or 30)
        except ValueError:
            dias = 30

        return render(request, 'polpa/validade.html', {
            'title': 'Validade',
            'lotes': ArmazenagemService.a_vencer(_filial(request), dias)[:300],
            'dias': dias,
            'hoje': timezone.localdate(),
            'resumo': ArmazenagemService.resumo(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'aprovar'),
        })


# ══════════════════════════════════════════════════════════════════════
# CÂMARAS
# ══════════════════════════════════════════════════════════════════════

class CamaraListView(PolpaBaseView):
    """As câmaras, com a ocupação de cada uma."""

    area = 'frio'

    def get(self, request):
        return render(request, 'polpa/camara_list.html', {
            'title': 'Câmaras frias',
            'linhas': ArmazenagemService.por_camara(_filial(request)),
            'inativas': Camara.objects.for_filial(_filial(request)).filter(ativo=False),
            'pode_agir': request.user.tem_permissao('polpa_frio', 'criar'),
        })


class CamaraFormView(PolpaBaseView):
    """Cadastra ou corrige uma câmara."""

    area = 'frio'
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        camara = self._buscar(request, pk) if pk else None
        return self._tela(
            request, CamaraForm(instance=camara, filial=_filial(request)), camara,
        )

    def post(self, request, pk=None):
        camara = self._buscar(request, pk) if pk else None
        form = CamaraForm(request.POST, instance=camara, filial=_filial(request))
        if not form.is_valid():
            return self._tela(request, form, camara)

        salva = form.save()
        messages.success(request, f'{salva} gravada.')
        return redirect(reverse('polpa:camara-list'))

    @staticmethod
    def _buscar(request, pk):
        return get_object_or_404(Camara.objects.for_filial(_filial(request)), pk=pk)

    @staticmethod
    def _tela(request, form, camara):
        return render(request, 'polpa/camara_form.html', {
            'title': str(camara) if camara else 'Nova câmara',
            'form': form,
            'camara': camara,
        })
