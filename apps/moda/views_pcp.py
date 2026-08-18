"""
Telas do PCP.

Cinco endereços, todos os que o menu já apontava (menos Ordens de Produção,
que é outro objeto e não este planejamento). Todas leem do mesmo
`PcpService`: se cada tela fizesse a própria conta, planejamento e
programação passariam a discordar sobre a mesma semana.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import CapacidadeSetorForm
from .models import CapacidadeSetor
from .services import PcpService
from .views import ModaBaseView

# Horizonte padrão. Oito semanas cobrem o prazo típico de uma confecção sem
# transformar a tabela numa faixa horizontal ilegível.
SEMANAS_PADRAO = 8
SEMANAS_MAX = 26


ABAS = (
    ('moda:pcp-planejamento', 'Planejamento'),
    ('moda:pcp-programacao', 'Programação'),
    ('moda:pcp-priorizacao', 'Priorização'),
    ('moda:pcp-acompanhamento', 'Acompanhamento'),
    ('moda:pcp-capacidade', 'Capacidade'),
)


def _filial(request):
    return request.filial_ativa


def _abas(ativa: str) -> dict:
    return {'abas': ABAS, 'aba_ativa': ativa}


def _semanas(request) -> int:
    try:
        pedido = int(request.GET.get('semanas') or SEMANAS_PADRAO)
    except ValueError:
        return SEMANAS_PADRAO
    return max(1, min(pedido, SEMANAS_MAX))


class PlanejamentoView(ModaBaseView):
    """Capacidade disponível × carga planejada, por setor e por máquina."""

    def get(self, request):
        semanas = _semanas(request)
        dados = PcpService.carga(_filial(request), semanas=semanas)
        return render(request, 'moda/pcp_planejamento.html', {
            'title': 'Planejamento de Produção',
            **_abas('moda:pcp-planejamento'),
            'semanas_escolhidas': semanas,
            **dados,
        })


class CapacidadeView(ModaBaseView):
    """Cadastro de quanto cada setor aguenta por semana."""

    permissao_acao = 'editar'

    def get(self, request):
        return self._render(request, CapacidadeSetorForm(filial=_filial(request)))

    def post(self, request):
        form = CapacidadeSetorForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            return self._render(request, form)

        capacidade = form.save(commit=False)
        capacidade.filial = _filial(request)
        capacidade.save()

        messages.success(
            request,
            f'{capacidade.get_setor_display()}: {capacidade.horas_semana}h por semana.',
        )
        return redirect(reverse('moda:pcp-capacidade'))

    @staticmethod
    def _render(request, form):
        return render(request, 'moda/pcp_capacidade.html', {
            'title': 'Capacidade Produtiva',
            **_abas('moda:pcp-capacidade'),
            'capacidades': CapacidadeSetor.objects.for_filial(_filial(request)),
            'form': form,
        })


class CapacidadeUpdateView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        capacidade = get_object_or_404(
            CapacidadeSetor.objects.for_filial(_filial(request)), pk=pk,
        )
        form = CapacidadeSetorForm(
            request.POST, instance=capacidade, filial=_filial(request),
        )
        if form.is_valid():
            form.save()
            messages.success(request, f'{capacidade.get_setor_display()} atualizado.')
        else:
            erros = '; '.join(f'{c}: {e[0]}' for c, e in form.errors.items())
            messages.error(request, f'Não salvo — {erros}')
        return redirect(reverse('moda:pcp-capacidade'))


class CapacidadeDeleteView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        capacidade = get_object_or_404(
            CapacidadeSetor.objects.for_filial(_filial(request)), pk=pk,
        )
        rotulo = capacidade.get_setor_display()
        capacidade.delete()
        messages.success(request, f'Capacidade de {rotulo} removida.')
        return redirect(reverse('moda:pcp-capacidade'))


class ProgramacaoView(ModaBaseView):
    """Os pedidos distribuídos pelas semanas."""

    def get(self, request):
        semanas = _semanas(request)
        return render(request, 'moda/pcp_programacao.html', {
            'title': 'Programação',
            **_abas('moda:pcp-programacao'),
            'semanas_escolhidas': semanas,
            'linhas': PcpService.programacao(_filial(request), semanas=semanas),
        })


class PriorizacaoView(ModaBaseView):
    """A fila: o que entra primeiro na fábrica, e o acumulado até cada um."""

    def get(self, request):
        return render(request, 'moda/pcp_fila.html', {
            'title': 'Priorização e Sequenciamento',
            **_abas('moda:pcp-priorizacao'),
            'linhas': PcpService.fila(_filial(request)),
        })


class AcompanhamentoView(ModaBaseView):
    """Onde cada pedido está agora, na ordem do fluxo."""

    def get(self, request):
        colunas = PcpService.acompanhamento(_filial(request))
        return render(request, 'moda/pcp_acompanhamento.html', {
            'title': 'Acompanhamento',
            **_abas('moda:pcp-acompanhamento'),
            'colunas': colunas,
            'total_pedidos': sum(c['quantidade'] for c in colunas),
            'total_atrasados': sum(c['atrasados'] for c in colunas),
        })
