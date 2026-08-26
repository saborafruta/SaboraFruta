"""
As telas do recebimento de fruta — a área da balança.

A TELA É DE QUEM ESTÁ NA PORTARIA às cinco da manhã, com um caminhão
esperando. Por isso a fila abre no que está em aberto, o romaneio salva com
o mínimo preenchido, e a decisão (aprovar/recusar) fica a um clique do
detalhe: cada campo a mais exigido na porta é um romaneio anotado em papel
para ser digitado depois — e é esse que se perde.
"""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.services.exceptions import DomainError

from .forms import (
    ClassificacaoForm, FrutaForm, FrutaRapidaForm, RecebimentoForm,
)
from .models import Fruta, Recebimento
from .services import RecebimentoService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _recebimento(request, pk) -> Recebimento:
    return get_object_or_404(
        Recebimento.objects.for_filial(_filial(request))
        .select_related('fruta', 'produtor', 'lote', 'classificado_por', 'decidido_por'),
        pk=pk,
    )


# ══════════════════════════════════════════════════════════════════════
# ROMANEIOS
# ══════════════════════════════════════════════════════════════════════

class RecebimentoListView(PolpaBaseView):
    """A fila da balança."""

    def get(self, request):
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'status': (request.GET.get('status') or '').strip(),
            'fruta': (request.GET.get('fruta') or '').strip(),
        }
        fila = RecebimentoService.fila(_filial(request), filtros)

        return render(request, 'polpa/recebimento_list.html', {
            'title': 'Recebimento de fruta',
            'recebimentos': fila[:200],
            'filtros': filtros,
            'tem_filtro': any(filtros.values()),
            'situacoes': Recebimento.Status.choices,
            'frutas': Fruta.objects.for_filial(_filial(request)).filter(ativo=True),
            'resumo': RecebimentoService.resumo(_filial(request)),
            'pode_agir': request.user.tem_permissao('polpa_recebimento', 'criar'),
        })


class RecusasView(PolpaBaseView):
    """
    As cargas que voltaram.

    É a MESMA fila, filtrada — e não uma tela paralela: duas consultas da
    mesma coisa divergem no dia em que alguém acrescenta um status, e aí as
    duas telas mostram números diferentes para a mesma pergunta.
    """

    def get(self, request):
        recusadas = RecebimentoService.fila(
            _filial(request), {'status': Recebimento.Status.RECUSADO},
        )
        return render(request, 'polpa/recebimento_list.html', {
            'title': 'Recusas e devoluções',
            'recebimentos': recusadas[:200],
            'filtros': {'status': Recebimento.Status.RECUSADO},
            'tem_filtro': True,
            'so_recusas': True,
            'situacoes': Recebimento.Status.choices,
            'frutas': Fruta.objects.for_filial(_filial(request)).filter(ativo=True),
            'resumo': RecebimentoService.resumo(_filial(request)),
            'pode_agir': False,
        })


class ClassificacaoFilaView(PolpaBaseView):
    """
    A bancada do laboratorio: as cargas esperando analise.

    A CLASSIFICACAO POR ROMANEIO JA' EXISTIA, dentro da tela do romaneio. O que
    faltava era a FILA -- e a fila e' que e' o trabalho de quem classifica. Sem
    ela, descobrir o que falta medir exigia abrir a lista de recebimentos,
    entrar em cada carga e olhar se ja' tinha analise. Com caminhao no patio,
    isso e' tempo que ninguem tem.

    A MESMA FILA DE SEMPRE, filtrada -- e nao uma consulta paralela. Duas
    consultas da mesma coisa divergem no dia em que alguem acrescenta um
    status, e ai' as duas telas respondem numeros diferentes para a mesma
    pergunta.

    DUAS LISTAS, PORQUE SAO DUAS ESPERAS. Em cima o que falta medir, que e' o
    trabalho de quem abre esta tela. Embaixo o que ja' foi medido e espera
    DECISAO -- que e' de outra pessoa, com outra permissao. Misturar as duas
    esconde a carga parada esperando alguem que nem sabe que ela existe.
    """

    area = 'recebimento'

    def get(self, request):
        filial = _filial(request)
        abertas = (
            RecebimentoService.fila(filial)
            .filter(status__in=Recebimento.ABERTOS)
            .order_by('data', 'numero')
        )
        # `classificado` e' propriedade (olha `classificado_em`), entao a
        # divisao acontece aqui e nao no banco. Sao as cargas abertas do
        # periodo: um punhado, nao uma tabela inteira.
        aguardando, medidas = [], []
        for carga in abertas:
            (medidas if carga.classificado else aguardando).append(carga)

        return render(request, 'polpa/classificacao_fila.html', {
            'title': 'Classificação',
            'aguardando': aguardando,
            'medidas': medidas,
            'resumo': RecebimentoService.resumo(filial),
            'pode_agir': request.user.tem_permissao('polpa_recebimento', 'editar'),
        })


class RecebimentoFormView(PolpaBaseView):
    """Abre ou corrige o romaneio."""

    permissao_acao = 'criar'

    def get(self, request, pk=None):
        recebimento = _recebimento(request, pk) if pk else None
        if recebimento and not recebimento.editavel:
            messages.error(request, 'Carga já decidida — o romaneio não muda mais.')
            return redirect(reverse('polpa:recebimento-detail', args=[pk]))

        form = RecebimentoForm(
            instance=recebimento, filial=_filial(request),
            initial={} if recebimento else {'data': timezone.localdate()},
        )
        return self._tela(request, form, recebimento)

    def post(self, request, pk=None):
        recebimento = _recebimento(request, pk) if pk else None
        if recebimento and not recebimento.editavel:
            messages.error(request, 'Carga já decidida — o romaneio não muda mais.')
            return redirect(reverse('polpa:recebimento-detail', args=[pk]))

        form = RecebimentoForm(
            request.POST, instance=recebimento, filial=_filial(request),
        )
        if not form.is_valid():
            return self._tela(request, form, recebimento)

        novo = form.save(commit=False)
        if not novo.pk:
            novo.criado_por = request.user
        novo.save()

        messages.success(
            request, f'Romaneio #{novo.numero:05d} gravado.',
        )
        return redirect(reverse('polpa:recebimento-detail', args=[novo.pk]))

    @staticmethod
    def _tela(request, form, recebimento):
        return render(request, 'polpa/recebimento_form.html', {
            'title': (
                f'Romaneio #{recebimento.numero:05d}' if recebimento
                else 'Novo recebimento'
            ),
            'form': form,
            'recebimento': recebimento,
        })


class RecebimentoDetailView(PolpaBaseView):
    """O romaneio inteiro: pesagem, classificação e a decisão."""

    def get(self, request, pk):
        recebimento = _recebimento(request, pk)
        return render(request, 'polpa/recebimento_detail.html', {
            'title': f'Romaneio #{recebimento.numero:05d}',
            'recebimento': recebimento,
            'form': ClassificacaoForm(instance=recebimento),
            # A LEITURA DA CARGA JÁ DECIDIDA. O formulário some quando não
            # há mais o que editar, e sem esta lista a tela ficaria muda
            # justamente sobre a medição que sustentou a decisão.
            'medicoes': [
                ('Temperatura', recebimento.temperatura_chegada),
                ('Brix', recebimento.brix),
                ('pH', recebimento.ph),
                ('Acidez', recebimento.acidez),
                ('Impureza %', recebimento.impureza),
                ('Danificada %', recebimento.danificada),
            ],
            'reprovacoes': recebimento.reprovacoes(),
            'pendencias': recebimento.pendencias(),
            'pode_agir': request.user.tem_permissao('polpa_recebimento', 'editar'),
            'pode_decidir': request.user.tem_permissao('polpa_recebimento', 'aprovar'),
        })


class ClassificarView(PolpaBaseView):
    """Grava a análise da carga."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        recebimento = _recebimento(request, pk)
        # QUEM VEIO DA FILA VOLTA PARA A FILA. Sao varias cargas em sequencia
        # na bancada; despejar a pessoa na tela de UM romaneio a cada analise
        # gravada faria ela navegar de volta seis vezes por manha. Quem veio da
        # tela do romaneio continua voltando para la'.
        if request.POST.get('voltar') == 'fila':
            volta = redirect(reverse('polpa:recebimento-classificacao'))
        else:
            volta = redirect(reverse('polpa:recebimento-detail', args=[pk]))

        form = ClassificacaoForm(request.POST, instance=recebimento)
        if not form.is_valid():
            messages.error(
                request,
                'Classificação não gravada: '
                + '; '.join(f'{c}: {e[0]}' for c, e in form.errors.items()),
            )
            return volta

        try:
            RecebimentoService.classificar(
                recebimento, form.cleaned_data, request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        desvios = recebimento.reprovacoes()
        if desvios:
            # O DESVIO APARECE NA HORA, e não só no relatório do mês: quem
            # está com o caminhão na porta ainda pode recusar.
            messages.warning(
                request, 'Classificação gravada, com desvio. ' + ' '.join(desvios),
            )
        else:
            messages.success(request, 'Classificação gravada — carga dentro da régua.')
        return volta


class AprovarView(PolpaBaseView):
    """Aceita a carga e faz nascer o lote."""

    permissao_acao = 'aprovar'

    def post(self, request, pk):
        recebimento = _recebimento(request, pk)
        try:
            lote = RecebimentoService.aprovar(recebimento, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(
                request,
                f'Carga aprovada. Lote {lote.numero_lote} criado com '
                f'{recebimento.peso_aceito} kg.',
            )
        return redirect(reverse('polpa:recebimento-detail', args=[pk]))


class RecusarView(PolpaBaseView):
    """Devolve a carga, com motivo."""

    permissao_acao = 'aprovar'

    def post(self, request, pk):
        recebimento = _recebimento(request, pk)
        try:
            RecebimentoService.recusar(
                recebimento, request.POST.get('motivo') or '', request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, 'Carga recusada e o motivo registrado.')
        return redirect(reverse('polpa:recebimento-detail', args=[pk]))


class CancelarView(PolpaBaseView):
    """Anula o romaneio digitado errado."""

    permissao_acao = 'cancelar'

    def post(self, request, pk):
        recebimento = _recebimento(request, pk)
        try:
            RecebimentoService.cancelar(
                recebimento, request.POST.get('motivo') or '', request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, 'Romaneio cancelado.')
        return redirect(reverse('polpa:recebimento-detail', args=[pk]))


# ══════════════════════════════════════════════════════════════════════
# FRUTAS — a régua que aprova ou recusa a carga
# ══════════════════════════════════════════════════════════════════════

class FrutaListView(PolpaBaseView):
    """O cadastro das frutas, com a régua e o rendimento esperado."""

    area = 'formulacao'

    def get(self, request):
        frutas = (
            Fruta.objects.for_filial(_filial(request))
            .select_related('produto')
        )
        return render(request, 'polpa/fruta_list.html', {
            'title': 'Frutas e rendimento padrão',
            'frutas': frutas,
            'mes': timezone.localdate().month,
            'pode_agir': request.user.tem_permissao('polpa_formulacao', 'criar'),
        })


class FrutaAjaxCreateView(PolpaBaseView):
    """
    Cadastra a fruta SEM SAIR do romaneio, e devolve a opcao pronta.

    A tela de novo recebimento era um beco: os dois selects vinham vazios numa
    filial nova, os campos sao obrigatorios, e nao havia como criar fruta dali.
    O caminho era abandonar o romaneio -- perdendo o que ja' foi digitado --,
    ir ao cadastro, voltar e comecar de novo. Com o motorista esperando na
    balanca, o que acontece de verdade e' a pesagem ir para um papel.
    """

    area = 'recebimento'
    permissao_acao = 'criar'

    def post(self, request):
        filial = _filial(request)
        form = FrutaRapidaForm(request.POST, filial=filial)
        if not form.is_valid():
            return JsonResponse(
                {
                    'ok': False,
                    'errors': {
                        campo: [erro['message'] for erro in mensagens]
                        for campo, mensagens in form.errors.get_json_data().items()
                    },
                },
                status=400,
            )
        fruta = form.save(commit=False)
        fruta.filial = filial
        fruta.save()
        return JsonResponse({'ok': True, 'id': fruta.pk, 'label': str(fruta)})


class FrutaFormView(PolpaBaseView):
    """Cadastra ou corrige a ficha da fruta."""

    area = 'formulacao'
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        fruta = self._buscar(request, pk) if pk else None
        return self._tela(request, FrutaForm(instance=fruta, filial=_filial(request)), fruta)

    def post(self, request, pk=None):
        fruta = self._buscar(request, pk) if pk else None
        form = FrutaForm(request.POST, instance=fruta, filial=_filial(request))
        if not form.is_valid():
            return self._tela(request, form, fruta)

        salva = form.save()
        messages.success(request, f'{salva} gravada.')
        return redirect(reverse('polpa:fruta-list'))

    @staticmethod
    def _buscar(request, pk):
        return get_object_or_404(Fruta.objects.for_filial(_filial(request)), pk=pk)

    @staticmethod
    def _tela(request, form, fruta):
        return render(request, 'polpa/fruta_form.html', {
            'title': str(fruta) if fruta else 'Nova fruta',
            'form': form,
            'fruta': fruta,
        })
