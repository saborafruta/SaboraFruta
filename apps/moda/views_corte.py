"""
Telas do controle de corte (grupo Produção).

A lista fica no endereço do menu (`producao/corte`) e traz os indicadores do
setor no topo: aproveitamento, perda e o comparativo entre tecido planejado
e utilizado. Os números vêm do mesmo serviço que a tela de cada corte usa —
duas contas para a mesma pergunta é como uma delas passa a mentir.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .forms import RegistroCorteForm
from .models import RegistroCorte, Tamanho
from .services import CorteService
from .services.integracao import IntegracaoService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _corte(request, pk) -> RegistroCorte:
    return get_object_or_404(
        RegistroCorte.objects.for_filial(_filial(request)).select_related(
            'ordem', 'ordem__pedido', 'ordem__pedido__cliente', 'ordem__item',
            'ordem__item__produto', 'ordem__item__modelo', 'ordem__item__tecido',
            'ordem__item__cor', 'tecido', 'cor',
        ).prefetch_related(
            'grade__tamanho', 'ordem__item__produto__ficha__materiais',
        ),
        pk=pk,
    )


class CorteListView(ModaBaseView):
    """Lista dos cortes com os indicadores do setor."""

    def get(self, request):
        cortes = list(
            RegistroCorte.objects.for_filial(_filial(request))
            .select_related(
                'ordem', 'ordem__pedido__cliente', 'ordem__item',
                'ordem__item__produto', 'tecido', 'cor',
            )
            .prefetch_related('ordem__item__produto__ficha__materiais')
        )

        status = (request.GET.get('status') or '').strip()
        if status in RegistroCorte.Status.values:
            cortes = [c for c in cortes if c.status == status]

        return render(request, 'moda/corte_list.html', {
            'title': 'Controle de Corte',
            'cortes': cortes,
            'status_escolhido': status,
            'status_choices': RegistroCorte.Status.choices,
            'indicadores': CorteService.indicadores(cortes),
        })


class CorteFormView(ModaBaseView):
    """Cria e edita o registro de corte."""

    permissao_acao = 'editar'

    def get(self, request, pk=None):
        corte = _corte(request, pk) if pk else None
        return self._render(request, RegistroCorteForm(
            instance=corte, filial=_filial(request),
        ), corte)

    def post(self, request, pk=None):
        corte = _corte(request, pk) if pk else None
        form = RegistroCorteForm(request.POST, instance=corte, filial=_filial(request))

        if not form.is_valid():
            return self._render(request, form, corte)

        novo = form.save(commit=False)
        novo.filial = _filial(request)
        try:
            CorteService.validar(novo)
        except DomainError as erro:
            messages.error(request, str(erro))
            return self._render(request, form, corte)

        novo.save()
        messages.success(request, f'Corte #{novo.numero:04d} salvo.')
        return redirect(reverse('moda:corte-detail', args=[novo.pk]))

    @staticmethod
    def _render(request, form, corte):
        return render(request, 'moda/corte_form.html', {
            'title': 'Editar corte' if corte else 'Novo corte',
            'form': form,
            'corte': corte,
        })


class CorteDetailView(ModaBaseView):
    """Um corte: encaixe, grade e o comparativo de tecido."""

    def get(self, request, pk):
        corte = _corte(request, pk)
        return render(request, 'moda/corte_detail.html', self.contexto(request, corte))

    @staticmethod
    def contexto(request, corte) -> dict:
        # O material e a quantidade que a baixa vai mexer, mostrados ANTES
        # do clique: mexer no estoque de outro módulo às cegas é como o
        # saldo errado começa.
        produto_estoque, a_baixar, impedimento = IntegracaoService.material_do_corte(corte)
        gravados = {g.tamanho_id: g.quantidade for g in corte.grade.all()}
        # Tamanhos da grade do produto quando existe; senão, todos os da
        # filial. Assim a tela não obriga a rolar 30 tamanhos para achar os
        # 6 que a peça usa.
        produto = corte.produto
        if produto and produto.grade_id:
            tamanhos = [i.tamanho for i in produto.grade.itens.select_related('tamanho').all()]
        else:
            tamanhos = list(Tamanho.objects.for_filial(_filial(request)).filter(ativo=True))

        return {
            'title': f'Corte #{corte.numero:04d}',
            'corte': corte,
            'alertas': CorteService.alertas(corte),
            'linhas_grade': [
                {'tamanho': t, 'quantidade': gravados.get(t.pk, 0)} for t in tamanhos
            ],
            'produto_estoque': produto_estoque,
            'a_baixar': a_baixar,
            'impedimento_baixa': impedimento,
            'pode_baixar': (
                not impedimento
                and not corte.estoque_baixado_em
                and corte.status == corte.Status.CORTADO
            ),
        }


class CorteGradeView(ModaBaseView):
    """Grava a grade do corte e sincroniza a quantidade."""

    permissao_acao = 'editar'

    def post(self, request, pk):
        corte = _corte(request, pk)

        quantidades = {}
        for chave, bruto in request.POST.items():
            if not chave.startswith('tam_'):
                continue
            try:
                quantidades[int(chave.removeprefix('tam_'))] = int(bruto or 0)
            except ValueError:
                continue

        total = CorteService.salvar_grade(corte, quantidades)
        messages.success(request, f'Grade salva — {total} peça(s) no corte.')
        return redirect(reverse('moda:corte-detail', args=[corte.pk]))


class CorteDeleteView(ModaBaseView):
    permissao_acao = 'excluir'

    def post(self, request, pk):
        corte = _corte(request, pk)
        # Corte já executado não se apaga: ele é o registro do tecido que
        # saiu do estoque. Cancelar preserva o histórico e tira dos
        # indicadores, que é o que se quer de verdade.
        if corte.status == RegistroCorte.Status.CORTADO:
            messages.error(
                request,
                'Corte já executado não pode ser excluído. Marque como cancelado.',
            )
            return redirect(reverse('moda:corte-detail', args=[corte.pk]))

        numero = corte.numero
        corte.delete()
        messages.success(request, f'Corte #{numero:04d} excluído.')
        return redirect(reverse('moda:corte-list'))


class CorteEstoqueView(ModaBaseView):
    """
    Dá baixa (ou estorna) o tecido que este corte consumiu.

    Um botão, e não automático ao marcar cortado: mexer no estoque de
    outro módulo é efeito colateral grande demais para acontecer sem
    alguém mandar. O consumo continua digitado uma vez só, aqui no corte.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        corte = _corte(request, pk)
        estornar = request.POST.get('acao') == 'estornar'

        try:
            if estornar:
                produto, quantidade = IntegracaoService.estornar_estoque_do_corte(
                    corte, request.user,
                )
                messages.success(
                    request,
                    f'Estorno feito: {quantidade} devolvido(s) ao estoque de {produto}.',
                )
            else:
                produto, quantidade = IntegracaoService.baixar_estoque_do_corte(
                    corte, request.user,
                )
                messages.success(
                    request,
                    f'Baixa registrada: {quantidade} de {produto} saíram do estoque.',
                )
        except DomainError as erro:
            messages.error(request, str(erro))
        return redirect(reverse('moda:corte-detail', args=[corte.pk]))