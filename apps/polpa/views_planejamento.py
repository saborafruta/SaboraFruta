"""
As telas do PPCP: sugestão, calendário, quadro e recursos.

TRÊS OLHARES SOBRE A MESMA PRODUÇÃO. A sugestão responde "o que produzir";
o calendário, "quando"; o quadro, "onde está". São a mesma ordem vista de
ângulos diferentes — nenhum deles guarda estado próprio, e por isso os três
nunca discordam.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.services.exceptions import DomainError

from .forms_recurso import RecursoForm
from .models import OrdemPolpa, Recurso
from .services import OrdemPolpaService, PlanejamentoService
from .views import PolpaBaseView

HORIZONTES = ((7, 'Semana'), (30, 'Mês'), (90, 'Trimestre'))


def _filial(request):
    return request.filial_ativa


class PlanejamentoView(PolpaBaseView):
    """O que produzir — a conta da necessidade, produto a produto."""

    area = 'producao'

    def get(self, request):
        try:
            horizonte = int(request.GET.get('horizonte') or 30)
        except ValueError:
            horizonte = 30
        if horizonte not in dict(HORIZONTES):
            horizonte = 30

        linhas = PlanejamentoService.sugestoes(_filial(request), horizonte)

        return render(request, 'polpa/planejamento.html', {
            'title': 'Planejamento de produção',
            'linhas': linhas,
            'a_produzir': [l for l in linhas if l['necessidade'] > 0],
            'horizonte': horizonte,
            'horizontes': HORIZONTES,
            'pode_agir': request.user.tem_permissao('polpa_producao', 'criar'),
        })


class GerarOrdemView(PolpaBaseView):
    """Abre a ordem a partir de uma sugestão."""

    area = 'producao'
    permissao_acao = 'criar'

    def post(self, request):
        from .models import Receita

        volta = redirect(
            reverse('polpa:planejamento')
            + f'?horizonte={request.POST.get("horizonte") or 30}'
        )
        receita = get_object_or_404(
            Receita.objects.for_filial(_filial(request)),
            pk=request.POST.get('receita'),
        )
        try:
            quantidade = Decimal(request.POST.get('quantidade') or '0')
        except (InvalidOperation, ValueError):
            messages.error(request, 'Quantidade inválida.')
            return volta

        try:
            op = OrdemPolpaService.criar(
                _filial(request), receita,
                {'quantidade_planejada': quantidade}, request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        # A ORDEM NASCE PLANEJADA, e não liberada: sugerir é uma coisa,
        # autorizar consumo é outra. Quem abre pela sugestão ainda decide
        # quando ela pode começar.
        messages.success(
            request,
            f'Ordem {op.numero} aberta com {quantidade} un, em planejamento.',
        )
        return redirect(reverse('polpa:ordem-detail', args=[op.pk]))


class CalendarioView(PolpaBaseView):
    """Quando produzir — as ordens dia a dia."""

    area = 'producao'

    def get(self, request):
        hoje = timezone.localdate()
        try:
            referencia = date.fromisoformat(request.GET.get('de') or hoje.isoformat())
        except ValueError:
            referencia = hoje

        # A SEMANA COMEÇA NA SEGUNDA, e a janela é de 14 dias: o mês inteiro
        # numa grade faz cada dia caber tão pouco que a ordem some dentro
        # dele, e é a ordem que se quer ver.
        inicio = referencia - timedelta(days=referencia.weekday())
        fim = inicio + timedelta(days=13)

        return render(request, 'polpa/calendario.html', {
            'title': 'Calendário de produção',
            'dias': PlanejamentoService.calendario(_filial(request), inicio, fim),
            'sem_data': PlanejamentoService.sem_data(_filial(request)),
            'capacidade': PlanejamentoService.carga_por_recurso(
                _filial(request), inicio, fim,
            )[0],
            'inicio': inicio,
            'fim': fim,
            'anterior': (inicio - timedelta(days=14)).isoformat(),
            'proximo': (inicio + timedelta(days=14)).isoformat(),
            'recursos': Recurso.objects.for_filial(_filial(request)).filter(ativo=True),
            'pode_agir': request.user.tem_permissao('polpa_producao', 'editar'),
        })


class ProgramarView(PolpaBaseView):
    """Marca o dia e o recurso de uma ordem."""

    area = 'producao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        op = get_object_or_404(
            OrdemPolpa.objects.for_filial(_filial(request)).select_related('ordem'),
            pk=pk,
        )
        volta = redirect(request.POST.get('voltar') or reverse('polpa:calendario'))

        quando = request.POST.get('dia') or ''
        try:
            dia = date.fromisoformat(quando)
        except ValueError:
            messages.error(request, 'Data inválida.')
            return volta

        recurso = None
        if request.POST.get('recurso'):
            recurso = get_object_or_404(
                Recurso.objects.for_filial(_filial(request)),
                pk=request.POST['recurso'],
            )

        try:
            PlanejamentoService.programar(
                op, timezone.make_aware(
                    timezone.datetime.combine(dia, timezone.datetime.min.time()),
                ),
                recurso,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request,
            f'Ordem {op.numero} programada para {dia:%d/%m}'
            + (f' em {recurso}.' if recurso else '.'),
        )
        return volta


class KanbanView(PolpaBaseView):
    """Onde está cada ordem — o quadro da fábrica."""

    area = 'producao'

    def get(self, request):
        return render(request, 'polpa/kanban.html', {
            'title': 'Quadro da produção',
            'colunas': PlanejamentoService.kanban(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_producao', 'editar'),
        })


# ══════════════════════════════════════════════════════════════════════
# RECURSOS
# ══════════════════════════════════════════════════════════════════════

class RecursoListView(PolpaBaseView):
    """Linhas e máquinas, com a capacidade de cada uma."""

    area = 'producao'

    def get(self, request):
        hoje = timezone.localdate()
        return render(request, 'polpa/recurso_list.html', {
            'title': 'Linhas e máquinas',
            'recursos': Recurso.objects.for_filial(_filial(request)),
            'carga': PlanejamentoService.carga_por_recurso(
                _filial(request), hoje, hoje + timedelta(days=6),
            )[0],
            'pode_agir': request.user.tem_permissao('polpa_producao', 'criar'),
        })


class RecursoFormView(PolpaBaseView):
    """Cadastra ou corrige um recurso."""

    area = 'producao'
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        recurso = self._buscar(request, pk) if pk else None
        return self._tela(request, RecursoForm(instance=recurso, filial=_filial(request)), recurso)

    def post(self, request, pk=None):
        recurso = self._buscar(request, pk) if pk else None
        form = RecursoForm(request.POST, instance=recurso, filial=_filial(request))
        if not form.is_valid():
            return self._tela(request, form, recurso)

        salvo = form.save()
        messages.success(request, f'{salvo} gravado.')
        return redirect(reverse('polpa:recurso-list'))

    @staticmethod
    def _buscar(request, pk):
        return get_object_or_404(Recurso.objects.for_filial(_filial(request)), pk=pk)

    # OS GRUPOS MORAM AQUI e o template recolhe o que sobrar num ultimo
    # cartao. Assim campo novo no formulario continua aparecendo na tela
    # sozinho -- que era a propriedade do laco antigo, e a que se perde quando
    # alguem escreve a lista de campos no template.
    GRUPOS = (
        ('Que recurso é', '', ('nome', 'tipo', 'linha_producao')),
        (
            'Quanto ele dá por dia',
            'É daqui que sai a capacidade do planejamento: sem estes números, '
            'a fábrica aparece com folga infinita e toda ordem cabe em qualquer dia.',
            ('capacidade_dia', 'horas_dia', 'setup_minutos'),
        ),
    )

    @staticmethod
    def _tela(request, form, recurso):
        agrupados = [
            nome
            for _titulo, _dica, campos in RecursoFormView.GRUPOS
            for nome in campos
        ]
        return render(request, 'polpa/recurso_form.html', {
            'title': str(recurso) if recurso else 'Novo recurso',
            'form': form,
            'recurso': recurso,
            'grupos': [
                (titulo, dica,
                 [form[nome] for nome in campos if nome in form.fields])
                for titulo, dica, campos in RecursoFormView.GRUPOS
            ],
            'campos_agrupados': agrupados,
        })
