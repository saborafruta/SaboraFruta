"""
Configurações Fiscais → Naturezas de operação.

É a tela que tira o fiscal do código. Sem ela a parametrização existe só no
banco, e mudar um CFOP volta a ser tarefa de quem tem acesso ao servidor — que
é exatamente o que a tabela veio evitar.
"""
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.fiscal.forms_natureza import NaturezaOperacaoForm, RegraNaturezaForm
from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao


def _filial(request):
    return request.filial_ativa


class NaturezaOperacaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'fiscal'
    template_name = 'fiscal/natureza/list.html'

    def get(self, request):
        filial = _filial(request)
        especie = (request.GET.get('especie') or '').strip()
        naturezas = (
            NaturezaOperacao.objects.for_filial(filial)
            .annotate(qtd_regras=Count('regras'))
            .order_by('especie', 'descricao')
        )
        if especie:
            naturezas = naturezas.filter(especie=especie)
        return render(request, self.template_name, {
            'title': 'Naturezas de operação',
            'naturezas': naturezas,
            'especie_filtro': especie,
            'especie_choices': NaturezaOperacao.Especie.choices,
            'pode_agir': request.user.tem_permissao('fiscal', 'editar'),
        })


class NaturezaOperacaoFormView(PermissaoRequiredMixin, View):
    permissao_modulo = 'fiscal'
    permissao_acao = 'editar'
    template_name = 'fiscal/natureza/form.html'

    def _instancia(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(
            NaturezaOperacao.objects.for_filial(_filial(request)), pk=pk,
        )

    def get(self, request, pk=None):
        natureza = self._instancia(request, pk)
        return render(request, self.template_name, {
            'title': str(natureza) if natureza else 'Nova natureza de operação',
            'form': NaturezaOperacaoForm(instance=natureza, filial=_filial(request)),
            'natureza': natureza,
            'regras': natureza.regras.select_related('produto') if natureza else [],
            'form_regra': (
                RegraNaturezaForm(filial=_filial(request)) if natureza else None
            ),
            'cancel_url': reverse('fiscal:natureza-list'),
        })

    def post(self, request, pk=None):
        natureza = self._instancia(request, pk)
        form = NaturezaOperacaoForm(
            request.POST, instance=natureza, filial=_filial(request),
        )
        if form.is_valid():
            salva = form.save(commit=False)
            salva.filial = _filial(request)
            salva.save()
            messages.success(request, 'Natureza de operação salva.')
            return redirect('fiscal:natureza-edit', pk=salva.pk)
        messages.error(request, 'Revise os dados da natureza.')
        return render(request, self.template_name, {
            'title': str(natureza) if natureza else 'Nova natureza de operação',
            'form': form, 'natureza': natureza,
            'regras': natureza.regras.select_related('produto') if natureza else [],
            'form_regra': (
                RegraNaturezaForm(filial=_filial(request)) if natureza else None
            ),
            'cancel_url': reverse('fiscal:natureza-list'),
        })


class RegraNaturezaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'fiscal'
    permissao_acao = 'editar'

    def post(self, request, pk):
        natureza = get_object_or_404(
            NaturezaOperacao.objects.for_filial(_filial(request)), pk=pk,
        )
        volta = redirect('fiscal:natureza-edit', pk=natureza.pk)
        form = RegraNaturezaForm(request.POST, filial=_filial(request))
        if not form.is_valid():
            # O ERRO PRECISA DIZER QUAL CAMPO: quem cadastra regra fiscal lida
            # com vinte campos parecidos, e "revise os dados" nao ajuda.
            detalhe = '; '.join(
                f'{form.fields[campo].label or campo}: {erros[0]}'
                for campo, erros in form.errors.items() if campo in form.fields
            ) or ' '.join(form.non_field_errors())
            messages.error(request, f'Regra não incluída. {detalhe}'.strip())
            return volta
        regra = form.save(commit=False)
        regra.natureza = natureza
        regra.save()
        messages.success(request, f'Regra CFOP {regra.cfop} incluída.')
        return volta


class RegraNaturezaDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'fiscal'
    permissao_acao = 'editar'

    def post(self, request, pk, regra_pk):
        natureza = get_object_or_404(
            NaturezaOperacao.objects.for_filial(_filial(request)), pk=pk,
        )
        regra = get_object_or_404(
            RegraNaturezaOperacao.objects.filter(natureza=natureza), pk=regra_pk,
        )
        regra.delete()
        messages.success(request, 'Regra removida.')
        return redirect('fiscal:natureza-edit', pk=natureza.pk)
