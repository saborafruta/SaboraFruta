"""Tela, historico e resultado da analise inteligente de compras."""
import json
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.cadastros.models import Fornecedor
from apps.compras.models import (
    CalculoTributarioCompra, CotacaoCompra, CotacaoCompraPreco,
)
from apps.compras.services.cotacao_compra_service import CotacaoCompraService
from apps.core.constants.tributacao import RegimeIBSCBS, regime_ibs_cbs_padrao
from apps.core.models import Empresa
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.produtos.models import Produto


def _regime_comprador(filial):
    return filial.regime_tributario or filial.empresa.regime_tributario


def _regime_ibs_comprador(filial):
    regime = _regime_comprador(filial)
    return filial.regime_ibs_cbs or filial.empresa.regime_ibs_cbs or regime_ibs_cbs_padrao(regime)


def _regime_fornecedor(fornecedor):
    return fornecedor.regime_tributario or ('simples_nacional' if fornecedor.optante_simples else '')


class CotacaoCompraNovaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    permissao_acao = 'criar'
    template_name = 'compras/cotacao/nova.html'

    def _contexto(self, request):
        filial = request.filial_ativa
        produtos = Produto.objects.for_filial(filial).filter(ativo=True).select_related(
            'unidade_medida',
        ).order_by('descricao')
        fornecedores = Fornecedor.objects.for_filial(filial).filter(ativo=True).order_by('razao_social')
        return {
            'empresa_nome': filial.empresa.nome_fantasia or filial.empresa.razao_social,
            'filial_nome': filial.nome_fantasia or filial.razao_social,
            'regime_comprador': _regime_comprador(filial),
            'regime_ibs_comprador': _regime_ibs_comprador(filial),
            'regimes_empresariais': Empresa.RegimeTributario.choices,
            'regimes_ibs_cbs': RegimeIBSCBS.choices,
            'data_referencia': timezone.localdate().isoformat(),
            'pode_editar_fornecedor': request.user.tem_permissao('cadastros', 'editar'),
            'pode_criar_fornecedor': request.user.tem_permissao('cadastros', 'criar'),
            'produtos_json': [{
                'id': produto.pk,
                'name': produto.descricao,
                'code': produto.codigo,
                'ncm': produto.ncm,
                'unit': getattr(produto.unidade_medida, 'sigla', '') or '',
            } for produto in produtos],
            'fornecedores_json': [{
                'id': fornecedor.pk,
                'name': str(fornecedor),
                'cnpj': fornecedor.cpf_cnpj,
                'regime': _regime_fornecedor(fornecedor),
                'ibs_cbs': fornecedor.regime_ibs_cbs or regime_ibs_cbs_padrao(_regime_fornecedor(fornecedor)),
            } for fornecedor in fornecedores],
        }

    def get(self, request):
        return render(request, self.template_name, self._contexto(request))

    def post(self, request):
        try:
            dados = json.loads(request.POST.get('payload') or '{}')
            cotacao = CotacaoCompraService.criar_e_analisar(
                filial=request.filial_ativa,
                usuario=request.user,
                dados=dados,
                pode_editar_fornecedor=request.user.tem_permissao('cadastros', 'editar'),
                pode_criar_fornecedor=request.user.tem_permissao('cadastros', 'criar'),
            )
        except (json.JSONDecodeError, DomainError, ValueError) as exc:
            messages.error(request, str(exc) or 'Nao foi possivel analisar a cotacao.')
            return render(request, self.template_name, self._contexto(request), status=400)
        messages.success(request, 'Analise concluida e salva no historico.')
        return redirect('compras:cotacao-detail', pk=cotacao.pk)


class CotacaoCompraListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    template_name = 'compras/cotacao/list.html'

    def get(self, request):
        qs = CotacaoCompra.objects.for_filial(request.filial_ativa).select_related('usuario')
        page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'cotacoes': page_obj.object_list,
        })


class CotacaoCompraDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'compras'
    template_name = 'compras/cotacao/detail.html'

    def get(self, request, pk):
        precos = CotacaoCompraPreco.objects.select_related(
            'fornecedor', 'calculo', 'calculo__regra',
        )
        cotacao = get_object_or_404(
            CotacaoCompra.objects.for_filial(request.filial_ativa)
            .select_related('usuario')
            .prefetch_related('fornecedores', Prefetch('itens__precos', queryset=precos), 'itens__produto'),
            pk=pk,
        )
        visao = request.GET.get('visao', CotacaoCompra.Visao.MENOR_CUSTO)
        if visao not in CotacaoCompra.Visao.values:
            visao = CotacaoCompra.Visao.MENOR_CUSTO
        rankings = []
        for item in cotacao.itens.all():
            linhas = []
            for preco in item.precos.all():
                calculo = preco.calculo
                linhas.append({
                    'fornecedor': preco.fornecedor,
                    'valor_unitario': preco.valor_unitario,
                    'valor_nominal': calculo.valor_bruto + calculo.custos_nao_recuperaveis - preco.desconto,
                    'credito_total': calculo.credito_total,
                    'custo_efetivo': calculo.custo_efetivo,
                    'regra_nome': calculo.regra_snapshot.get('regra_nome'),
                    'sem_regra': not calculo.regra_snapshot.get('regra_id'),
                })
            if visao == CotacaoCompra.Visao.MENOR_PRECO:
                linhas.sort(key=lambda linha: (linha['valor_unitario'], linha['custo_efetivo']))
            elif visao == CotacaoCompra.Visao.MAIOR_CREDITO:
                linhas.sort(key=lambda linha: (-linha['credito_total'], linha['custo_efetivo']))
            else:
                linhas.sort(key=lambda linha: (linha['custo_efetivo'], linha['valor_unitario']))
            for posicao, linha in enumerate(linhas, start=1):
                linha['posicao'] = posicao
                linha['melhor'] = posicao == 1
            economia_segunda = linhas[1]['custo_efetivo'] - linhas[0]['custo_efetivo'] if len(linhas) > 1 else Decimal('0')
            economia_unidade = economia_segunda / item.quantidade if item.quantidade else Decimal('0')
            melhor_custo_unitario = linhas[0]['custo_efetivo'] / item.quantidade if item.quantidade else Decimal('0')
            ultima_compra = dict(item.ultima_compra_snapshot or {})
            variacao_percentual = None
            variacao_tipo = ''
            if ultima_compra:
                try:
                    ultima_compra['data_compra'] = date.fromisoformat(ultima_compra['data_compra'])
                    ultimo_custo = Decimal(ultima_compra['custo_unitario'])
                except (KeyError, TypeError, ValueError):
                    ultima_compra = {}
                else:
                    if ultimo_custo > 0:
                        variacao_percentual = (
                            (melhor_custo_unitario - ultimo_custo) / ultimo_custo
                        ) * Decimal('100')
                        variacao_tipo = (
                            'menor' if variacao_percentual < 0
                            else 'maior' if variacao_percentual > 0
                            else 'igual'
                        )
            rankings.append({
                'item': item,
                'linhas': linhas,
                'melhor': linhas[0],
                'economia_segunda': economia_segunda,
                'economia_unidade': economia_unidade,
                'melhor_custo_unitario': melhor_custo_unitario,
                'ultima_compra': ultima_compra,
                'variacao_percentual': variacao_percentual,
                'variacao_percentual_abs': abs(variacao_percentual) if variacao_percentual is not None else None,
                'variacao_tipo': variacao_tipo,
            })

        grupos = OrderedDict()
        vencedores = CalculoTributarioCompra.objects.filter(
            preco__item__cotacao=cotacao, vencedor=True,
        ).select_related('preco__item', 'preco__fornecedor')
        for calculo in vencedores:
            participante = calculo.preco.fornecedor
            grupo = grupos.setdefault(participante.pk, {
                'fornecedor': participante,
                'itens': [],
                'nominal': Decimal('0'),
                'creditos': Decimal('0'),
                'efetivo': Decimal('0'),
            })
            grupo['itens'].append(calculo.preco.item)
            grupo['nominal'] += calculo.valor_bruto + calculo.custos_nao_recuperaveis - calculo.preco.desconto
            grupo['creditos'] += calculo.credito_total
            grupo['efetivo'] += calculo.custo_efetivo

        return render(request, self.template_name, {
            'cotacao': cotacao,
            'rankings': rankings,
            'grupos': list(grupos.values()),
            'visao': visao,
            'visoes': CotacaoCompra.Visao.choices,
            'tem_calculo_sem_regra': any(
                linha['sem_regra'] for ranking in rankings for linha in ranking['linhas']
            ),
        })
