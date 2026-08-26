"""
Cadastro das etapas que a indústria cria.

A RÉGUA DO VOCABULÁRIO COMUM APARECE AO LADO da lista, e não numa ajuda
escondida. A `sequencia` é o campo que decide se a etapa nova serve para
alguma coisa: sem saber que despolpamento é 10 e envase é 23, a pessoa chuta —
e uma fermentação com sequência 99 cai depois do congelamento, que não
descreve fábrica nenhuma.

NÃO SE APAGA ETAPA JÁ APONTADA. O código dela está gravado nos apontamentos, e
sem cadastro eles perdem nome e posição: viram "fermentacao" em minúscula no
fim da lista, num relatório que alguém vai ler seis meses depois. Desativar
tira da escolha e preserva o passado.
"""
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.polpa.forms_etapa import EtapaProcessoForm
from apps.polpa.models import ApontamentoEtapa, EtapaProcesso
from apps.polpa.models.processo import SEQUENCIA, Etapa

from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _regua() -> list[dict]:
    """
    As trinta e quatro canônicas com a posição de cada uma.

    É o que a pessoa precisa ter à vista para escolher onde a etapa nova
    entra — e é gerado da própria `SEQUENCIA`, não digitado, para não
    envelhecer no dia em que uma etapa for acrescentada ao vocabulário.
    """
    rotulos = dict(Etapa.choices)
    return [
        {'posicao': indice, 'nome': rotulos[codigo]}
        for indice, codigo in enumerate(SEQUENCIA)
    ]


class EtapaListView(PolpaBaseView):
    """As etapas próprias da casa, com quantas ordens já as apontaram."""

    area = 'producao'

    def get(self, request):
        filial = _filial(request)
        etapas = list(EtapaProcesso.objects.for_filial(filial))

        # QUANTAS VEZES CADA UMA FOI APONTADA: é o que diz se dá para
        # desativá-la sem deixar histórico órfão, e é a pergunta que a pessoa
        # faz antes de mexer.
        #
        # Contado numa consulta só e pendurado no objeto: uma contagem por
        # etapa dentro do laço do template daria uma consulta por linha, e a
        # tela ficaria lenta justamente na fábrica que mais cadastrou etapas.
        contagem = dict(
            ApontamentoEtapa.objects.for_filial(filial)
            .filter(etapa__in=[e.codigo for e in etapas])
            .values_list('etapa')
            .annotate(total=Count('pk'))
        )
        for etapa in etapas:
            etapa.usos = contagem.get(etapa.codigo, 0)

        return render(request, 'polpa/etapa_list.html', {
            'title': 'Etapas do processo',
            'etapas': etapas,
            'regua': _regua(),
            'pode_agir': request.user.tem_permissao('polpa_producao', 'criar'),
        })


class EtapaFormView(PolpaBaseView):
    """Cria ou corrige uma etapa da casa."""

    area = 'producao'
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        etapa = self._buscar(request, pk) if pk else None
        return self._tela(
            request,
            EtapaProcessoForm(instance=etapa, filial=_filial(request)),
            etapa,
        )

    def post(self, request, pk=None):
        etapa = self._buscar(request, pk) if pk else None
        form = EtapaProcessoForm(
            request.POST, instance=etapa, filial=_filial(request),
        )
        if not form.is_valid():
            return self._tela(request, form, etapa)

        salva = form.save()
        messages.success(request, f'Etapa "{salva.nome}" gravada.')
        # A ETAPA NÃO ENTRA SOZINHA NAS RECEITAS. Cadastrá-la é criar o
        # vocabulário; quem decide em quais produtos ela acontece é a receita.
        if etapa is None:
            messages.info(
                request,
                'Agora declare-a nas receitas que passam por ela — cadastrar '
                'a etapa não a coloca em produto nenhum.',
            )
        return redirect(reverse('polpa:etapa-list'))

    @staticmethod
    def _buscar(request, pk):
        return get_object_or_404(
            EtapaProcesso.objects.for_filial(_filial(request)), pk=pk,
        )

    @staticmethod
    def _tela(request, form, etapa):
        return render(request, 'polpa/etapa_form.html', {
            'title': str(etapa) if etapa else 'Nova etapa',
            'form': form,
            'etapa': etapa,
            'regua': _regua(),
        })
