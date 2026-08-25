"""
As telas da receita: a lista por produto e a ficha aberta.

A FICHA INTEIRA NUMA TELA — cabeçalho, ingredientes, embalagem, etapas e
custos. Separar em abas faria a pessoa fechar a receita sem ver o custo que
ela acabou de mudar, e o custo é justamente o que se olha ao mexer numa
fórmula.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError
from apps.producao.models import FichaTecnica, ItemFichaTecnica

from .forms_receita import EtapaReceitaForm, ItemReceitaForm, ReceitaForm
from .models import EtapaReceita, Receita
from .services import ReceitaService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _receita(request, pk) -> Receita:
    return get_object_or_404(
        Receita.objects.for_filial(_filial(request))
        .select_related('ficha', 'ficha__produto_acabado'),
        pk=pk,
    )


class ReceitaListView(PolpaBaseView):
    """As receitas, agrupadas por produto."""

    area = 'formulacao'

    def get(self, request):
        busca = (request.GET.get('busca') or '').strip()
        receitas = (
            Receita.objects.for_filial(_filial(request))
            .select_related('ficha', 'ficha__produto_acabado')
            .order_by('ficha__produto_acabado__descricao', '-ficha__versao')
        )
        if busca:
            receitas = receitas.filter(
                ficha__produto_acabado__descricao__icontains=busca,
            )

        # AGRUPADO POR PRODUTO porque a pergunta é "qual receita está valendo
        # para este produto" — uma lista corrida de versões faria procurar a
        # ativa no meio das antigas.
        grupos: dict = {}
        for receita in receitas:
            grupos.setdefault(receita.produto, []).append(receita)

        return render(request, 'polpa/receita_list.html', {
            'title': 'Formulações',
            'grupos': [
                {
                    'produto': produto,
                    'versoes': versoes,
                    'ativa': next((r for r in versoes if r.ativa), None),
                }
                for produto, versoes in grupos.items()
            ],
            'busca': busca,
            'total': len(receitas),
            'pode_agir': request.user.tem_permissao('polpa_formulacao', 'criar'),
        })


class ReceitaFormView(PolpaBaseView):
    """Abre a receita ou corrige o cabeçalho dela."""

    area = 'formulacao'
    permissao_acao = 'criar'

    def get(self, request, pk=None):
        receita = _receita(request, pk) if pk else None
        form = ReceitaForm(filial=_filial(request), receita=receita)
        return self._tela(request, form, receita)

    def post(self, request, pk=None):
        receita = _receita(request, pk) if pk else None
        form = ReceitaForm(request.POST, filial=_filial(request), receita=receita)
        if not form.is_valid():
            return self._tela(request, form, receita)

        dados = form.cleaned_data
        if receita is None:
            try:
                receita = ReceitaService.criar(
                    _filial(request), dados['produto'], dados,
                )
            except Exception as erro:  # noqa: BLE001 - vira mensagem na tela
                messages.error(request, f'Não foi possível criar a receita: {erro}')
                return self._tela(request, form, None)
        else:
            ficha = receita.ficha
            ficha.descricao = dados['descricao']
            ficha.versao = dados['versao']
            ficha.quantidade_produzida = dados['quantidade_produzida']
            ficha.tempo_producao_minutos = dados.get('tempo_producao_minutos') or 0
            ficha.custo_mao_obra_padrao = dados.get('custo_mao_obra_padrao') or 0
            ficha.custo_indireto_padrao = dados.get('custo_indireto_padrao') or 0
            ficha.save()

            receita.rendimento_esperado = dados.get('rendimento_esperado')
            receita.temperatura_processo_min = dados.get('temperatura_processo_min')
            receita.temperatura_processo_max = dados.get('temperatura_processo_max')
            receita.observacoes_tecnicas = dados.get('observacoes_tecnicas') or ''
            receita.save()

        messages.success(request, f'Receita {receita.produto} v{receita.versao} gravada.')
        return redirect(reverse('polpa:receita-detail', args=[receita.pk]))

    @staticmethod
    def _tela(request, form, receita):
        return render(request, 'polpa/receita_form.html', {
            'title': str(receita) if receita else 'Nova formulação',
            'form': form,
            'receita': receita,
            # SEM PRODUTO ACABADO CADASTRADO não há receita possível, e um
            # select vazio não diz por quê.
            'tem_produto': form.fields['produto'].queryset.exists(),
        })


class ReceitaDetailView(PolpaBaseView):
    """A ficha aberta: ingredientes, embalagem, etapas e custos."""

    area = 'formulacao'

    def get(self, request, pk):
        receita = _receita(request, pk)
        separados = ReceitaService.itens(receita)
        real = ReceitaService.rendimento_real(receita)

        return render(request, 'polpa/receita_detail.html', {
            'title': f'{receita.produto} v{receita.versao}',
            'receita': receita,
            'ingredientes': separados['ingredientes'],
            'embalagens': separados['embalagens'],
            'base': separados['base'],
            'etapas': receita.etapas.all(),
            'custos': ReceitaService.custos(receita),
            'real': real,
            'desvio': ReceitaService.desvio_de_rendimento(receita),
            'pendencias': receita.pendencias(),
            'form_item': ItemReceitaForm(filial=_filial(request), ficha=receita.ficha),
            'form_etapa': EtapaReceitaForm(
                initial={'ordem': receita.etapas.count() + 1},
            ),
            'pode_agir': request.user.tem_permissao('polpa_formulacao', 'editar'),
            'pode_ativar': request.user.tem_permissao('polpa_formulacao', 'aprovar'),
        })


class ItemAddView(PolpaBaseView):
    """Lança um insumo na receita."""

    area = 'formulacao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        receita = _receita(request, pk)
        volta = redirect(reverse('polpa:receita-detail', args=[pk]))

        form = ItemReceitaForm(
            request.POST, filial=_filial(request), ficha=receita.ficha,
        )
        if not form.is_valid():
            messages.error(
                request,
                'Insumo não lançado: '
                + '; '.join(f'{c}: {e[0]}' for c, e in form.errors.items()),
            )
            return volta

        item = form.save(commit=False)
        item.ficha = receita.ficha
        item.save()
        messages.success(request, f'{item.materia_prima} lançado na receita.')
        return volta


class ItemRemoveView(PolpaBaseView):
    """Tira um insumo da receita."""

    area = 'formulacao'
    permissao_acao = 'editar'

    def post(self, request, pk, item_pk):
        receita = _receita(request, pk)
        item = get_object_or_404(
            ItemFichaTecnica.objects.filter(ficha=receita.ficha), pk=item_pk,
        )
        nome = str(item.materia_prima)
        item.delete()
        messages.success(request, f'{nome} removido da receita.')
        return redirect(reverse('polpa:receita-detail', args=[pk]))


class EtapaAddView(PolpaBaseView):
    """Acrescenta uma etapa ao processo."""

    area = 'formulacao'
    permissao_acao = 'editar'

    def post(self, request, pk):
        receita = _receita(request, pk)
        volta = redirect(reverse('polpa:receita-detail', args=[pk]))

        form = EtapaReceitaForm(request.POST)
        if not form.is_valid():
            messages.error(
                request,
                'Etapa não gravada: '
                + '; '.join(f'{c}: {e[0]}' for c, e in form.errors.items()),
            )
            return volta

        etapa = form.save(commit=False)
        etapa.receita = receita
        etapa.save()
        messages.success(request, f'Etapa {etapa.nome} gravada.')
        return volta


class EtapaRemoveView(PolpaBaseView):
    """Tira uma etapa do processo."""

    area = 'formulacao'
    permissao_acao = 'editar'

    def post(self, request, pk, etapa_pk):
        receita = _receita(request, pk)
        etapa = get_object_or_404(
            EtapaReceita.objects.filter(receita=receita), pk=etapa_pk,
        )
        nome = etapa.nome
        etapa.delete()
        messages.success(request, f'Etapa {nome} removida.')
        return redirect(reverse('polpa:receita-detail', args=[pk]))


class NovaVersaoView(PolpaBaseView):
    """Copia a receita numa versão nova."""

    area = 'formulacao'
    permissao_acao = 'criar'

    def post(self, request, pk):
        receita = _receita(request, pk)
        try:
            nova = ReceitaService.nova_versao(receita, request.POST.get('versao') or '')
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('polpa:receita-detail', args=[pk]))

        messages.success(
            request,
            f'Versão {nova.versao} criada em rascunho, com os mesmos '
            'ingredientes e etapas. A anterior continua valendo até você '
            'ativar esta.',
        )
        return redirect(reverse('polpa:receita-detail', args=[nova.pk]))


class AtivarView(PolpaBaseView):
    """Põe a versão em uso."""

    area = 'formulacao'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        receita = _receita(request, pk)
        try:
            ReceitaService.ativar(receita)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(
                request,
                f'Versão {receita.versao} ativada — é ela que a produção passa '
                'a usar.',
            )
        return redirect(reverse('polpa:receita-detail', args=[pk]))
