"""Views de Contas a Pagar."""
from __future__ import annotations

from decimal import Decimal
import mimetypes
from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import RegistroAuditoria
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.forms.pagar import ContaPagarForm, PagamentoContaPagarForm
from apps.financeiro.models.conta_bancaria import PlanoContas
from apps.financeiro.models.receber_pagar import ContaPagar, PagamentoContaPagar
from apps.financeiro.services.pagar_service import ContaPagarService
from apps.financeiro.services.dashboard_contas_service import DashboardContasService

STATUS_CHOICES = StatusContaPagar.choices

PILL_STATUS = {
    StatusContaPagar.ABERTO:    'is-blue',
    StatusContaPagar.PAGO:      'is-green',
    StatusContaPagar.VENCIDO:   'is-red',
    StatusContaPagar.CANCELADO: 'is-slate',
    StatusContaPagar.AGENDADO:  'is-amber',
}


def _filial(request):
    return request.filial_ativa


def _usuario_admin(request):
    perfil = getattr(request.user, 'perfil', None)
    return request.user.is_superuser or bool(perfil and perfil.is_admin)


def _logs_edicao_valor(conta):
    return RegistroAuditoria.objects.filter(
        objeto_tipo=conta._meta.label_lower,
        objeto_id=conta.pk,
        modulo=RegistroAuditoria.Modulo.FINANCEIRO,
        acao=RegistroAuditoria.Acao.AJUSTAR,
    ).select_related('usuario')[:20]


def _categorias_financeiras_filtro(request):
    """Monta os tres niveis e normaliza a selecao da categoria financeira."""
    categorias_base = PlanoContas.objects.filter(
        empresa=_filial(request).empresa,
        tipo='D',
    )
    grupos = list(categorias_base.filter(nivel=1).order_by('codigo'))
    subgrupos = list(
        categorias_base.filter(nivel=2, conta_pai__isnull=False)
        .select_related('conta_pai')
        .order_by('codigo')
    )
    categorias = list(
        categorias_base.filter(nivel=3, conta_pai__isnull=False)
        .select_related('conta_pai__conta_pai')
        .order_by('codigo')
    )

    grupo_id = request.GET.get('categoria_grupo', '').strip()
    subgrupo_id = request.GET.get('categoria_subgrupo', '').strip()
    categoria_id = request.GET.get('categoria_financeira', '').strip()

    categoria_selecionada = next(
        (categoria for categoria in categorias if str(categoria.pk) == categoria_id),
        None,
    )
    if categoria_selecionada:
        categoria_id = str(categoria_selecionada.pk)
        subgrupo_id = str(categoria_selecionada.conta_pai_id)
        grupo_id = str(categoria_selecionada.conta_pai.conta_pai_id or '')
    else:
        categoria_id = ''
        subgrupo_selecionado = next(
            (subgrupo for subgrupo in subgrupos if str(subgrupo.pk) == subgrupo_id),
            None,
        )
        if subgrupo_selecionado:
            subgrupo_id = str(subgrupo_selecionado.pk)
            grupo_id = str(subgrupo_selecionado.conta_pai_id)
        else:
            subgrupo_id = ''
            grupo_selecionado = next(
                (grupo for grupo in grupos if str(grupo.pk) == grupo_id),
                None,
            )
            grupo_id = str(grupo_selecionado.pk) if grupo_selecionado else ''

    return {
        'categoria_grupos': grupos,
        'categoria_subgrupos': subgrupos,
        'categorias_financeiras': categorias,
        'categoria_grupo_filtro': grupo_id,
        'categoria_subgrupo_filtro': subgrupo_id,
        'categoria_financeira_filtro': categoria_id,
        'categoria_financeira_selecionada': categoria_selecionada,
    }


def _aplicar_filtro_categoria_financeira(qs, categoria_contexto):
    categoria_id = categoria_contexto['categoria_financeira_filtro']
    subgrupo_id = categoria_contexto['categoria_subgrupo_filtro']
    grupo_id = categoria_contexto['categoria_grupo_filtro']
    if categoria_id:
        return qs.filter(plano_contas_id=categoria_id)
    if subgrupo_id:
        return qs.filter(plano_contas__conta_pai_id=subgrupo_id)
    if grupo_id:
        return qs.filter(plano_contas__conta_pai__conta_pai_id=grupo_id)
    return qs


def _kpis(qs_base):
    hoje = timezone.localdate()
    primeiro_dia_mes = hoje.replace(day=1)

    totais = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO, StatusContaPagar.AGENDADO]
    ).aggregate(
        total_aberto=Sum('valor_saldo'),
        qtd_aberto=Count('id'),
    )

    vencido = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO],
        data_vencimento__lt=hoje,
    ).aggregate(total_vencido=Sum('valor_saldo'))

    pago_mes = qs_base.filter(
        status=StatusContaPagar.PAGO,
        data_pagamento__gte=primeiro_dia_mes,
    ).aggregate(total_mes=Sum('valor_pago'))

    vence_hoje = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO],
        data_vencimento=hoje,
    ).aggregate(total_hoje=Sum('valor_saldo'), qtd_hoje=Count('id'))

    return {
        'kpi_total_aberto':  totais['total_aberto']    or 0,
        'kpi_qtd_aberto':    totais['qtd_aberto']      or 0,
        'kpi_total_vencido': vencido['total_vencido']  or 0,
        'kpi_total_mes':     pago_mes['total_mes']     or 0,
        'kpi_total_hoje':    vence_hoje['total_hoje']  or 0,
        'kpi_qtd_hoje':      vence_hoje['qtd_hoje']    or 0,
    }


class ContaPagarListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        filial = _filial(request)
        ContaPagarService.atualizar_status_vencidos(filial)

        qs = (
            ContaPagar.objects.for_filial(filial)
            .select_related('fornecedor', 'funcionario', 'forma_pagamento')
            .order_by('data_vencimento')
        )

        kpis = _kpis(qs)

        status = request.GET.get('status', '')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')
        categoria_contexto = _categorias_financeiras_filtro(request)

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(fornecedor__razao_social__icontains=q)
                | Q(fornecedor__nome_fantasia__icontains=q)
                | Q(funcionario__nome__icontains=q)
                | Q(funcionario__cpf__icontains=q)
                | Q(documento_numero__icontains=q)
                | Q(nota_fiscal_fornecedor__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_vencimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_vencimento__lte=data_fim)
        qs = _aplicar_filtro_categoria_financeira(qs, categoria_contexto)

        totais_filtro = qs.aggregate(
            total_valor=Sum('valor_final'),
            total_saldo=Sum('valor_saldo'),
            total_pago=Sum('valor_pago'),
        )

        paginator = Paginator(qs, 50)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        qd = request.GET.copy()
        qd.pop('page', None)
        page_querystring = qd.urlencode()

        pode_criar = request.user.tem_permissao('financeiro', 'criar')
        pode_editar = request.user.tem_permissao('financeiro', 'editar')

        return render(request, 'financeiro/pagar/list.html', {
            'title': 'Contas a Pagar',
            'page_obj': page_obj,
            'contas': page_obj,
            'status_choices': STATUS_CHOICES,
            'status_filtro': status,
            'q': q,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'totais_filtro': totais_filtro,
            'page_querystring': page_querystring,
            'pill_status': PILL_STATUS,
            'pode_criar': pode_criar,
            'pode_editar': pode_editar,
            'today': timezone.localdate(),
            'dashboard_contas': DashboardContasService.apurar(filial),
            **categoria_contexto,
            **kpis,
        })


def _filtrar_contas_pagas(request):
    """Retorna o historico de contas pagas da filial com os filtros da tela."""
    qs = (
        ContaPagar.objects.for_filial(_filial(request))
        .filter(status=StatusContaPagar.PAGO)
        .select_related(
            'fornecedor', 'funcionario', 'forma_pagamento', 'conta_bancaria',
            'plano_contas', 'conta_contabil',
        )
    )

    q = request.GET.get('q', '').strip()
    data_ini = request.GET.get('data_ini', '')
    data_fim = request.GET.get('data_fim', '')
    ordenacao = request.GET.get('ordenacao', 'recentes')
    categoria_contexto = _categorias_financeiras_filtro(request)

    if q:
        qs = qs.filter(
            Q(fornecedor__razao_social__icontains=q)
            | Q(fornecedor__nome_fantasia__icontains=q)
            | Q(fornecedor__cpf_cnpj__icontains=q)
            | Q(funcionario__nome__icontains=q)
            | Q(funcionario__cpf__icontains=q)
            | Q(documento_numero__icontains=q)
            | Q(nota_fiscal_fornecedor__icontains=q)
            | Q(pagamentos__referencia_pagamento__icontains=q)
        ).distinct()
    if data_ini:
        qs = qs.filter(data_pagamento__gte=data_ini)
    if data_fim:
        qs = qs.filter(data_pagamento__lte=data_fim)
    qs = _aplicar_filtro_categoria_financeira(qs, categoria_contexto)

    ordenacoes = {
        'recentes': ('-data_pagamento', '-id'),
        'antigas': ('data_pagamento', 'id'),
        'maior_valor': ('-valor_pago', '-data_pagamento'),
        'menor_valor': ('valor_pago', '-data_pagamento'),
        'beneficiario': ('fornecedor__razao_social', 'funcionario__nome', '-data_pagamento'),
    }
    return qs.order_by(*ordenacoes.get(ordenacao, ordenacoes['recentes'])), {
        'q': q,
        'data_ini': data_ini,
        'data_fim': data_fim,
        'ordenacao': ordenacao,
        **categoria_contexto,
    }


class ContaPagaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        qs, filtros = _filtrar_contas_pagas(request)
        totais = qs.aggregate(
            quantidade=Count('id'),
            valor_original=Sum('valor_original'),
            valor_pago=Sum('valor_pago'),
            juros=Sum('valor_juros'),
            multas=Sum('valor_multa'),
            descontos=Sum('valor_desconto'),
        )
        totais['acrescimos'] = (totais['juros'] or 0) + (totais['multas'] or 0)
        paginator = Paginator(qs, 40)
        page_obj = paginator.get_page(request.GET.get('page', 1))
        query = request.GET.copy()
        query.pop('page', None)

        return render(request, 'financeiro/pagar/pagas.html', {
            'title': 'Contas Pagas',
            'contas': page_obj,
            'page_obj': page_obj,
            'totais': totais,
            'page_querystring': query.urlencode(),
            'pode_criar': request.user.tem_permissao('financeiro', 'criar'),
            **filtros,
        })


class ContaPagaRelatorioView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        qs, filtros = _filtrar_contas_pagas(request)
        totais = qs.aggregate(
            quantidade=Count('id'),
            valor_original=Sum('valor_original'),
            valor_pago=Sum('valor_pago'),
            juros=Sum('valor_juros'),
            multas=Sum('valor_multa'),
            descontos=Sum('valor_desconto'),
        )
        totais['acrescimos'] = (totais['juros'] or 0) + (totais['multas'] or 0)
        return render(request, 'financeiro/pagar/relatorio_pagas.html', {
            'title': 'Relatorio de Contas Pagas',
            'contas': list(qs),
            'totais': totais,
            'filial': _filial(request),
            'gerado_em': timezone.localtime(),
            **filtros,
        })


class ContaPagarRelatorioView(PermissaoRequiredMixin, View):
    """Relatório imprimível: agrupa os títulos (por padrão em aberto) por
    fornecedor, com a nota de entrada vinculada de cada título."""

    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        from apps.compras.models import EntradaNF

        filial = _filial(request)

        status = request.GET.get('status', '')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')
        categoria_contexto = _categorias_financeiras_filtro(request)

        qs = (
            ContaPagar.objects.for_filial(filial)
            .select_related('fornecedor', 'funcionario')
            .order_by('data_vencimento')
        )
        if status:
            qs = qs.filter(status=status)
        else:
            # Sem status escolhido, o relatório foca nos títulos em aberto.
            qs = qs.filter(status__in=[
                StatusContaPagar.ABERTO,
                StatusContaPagar.VENCIDO,
                StatusContaPagar.AGENDADO,
            ])
        if q:
            qs = qs.filter(
                Q(fornecedor__razao_social__icontains=q)
                | Q(funcionario__nome__icontains=q)
                | Q(documento_numero__icontains=q)
                | Q(nota_fiscal_fornecedor__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_vencimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_vencimento__lte=data_fim)
        qs = _aplicar_filtro_categoria_financeira(qs, categoria_contexto)

        titulos = list(qs)

        # Carrega as notas de entrada vinculadas (documento_tipo="entrada_nf")
        # só para exibir número/data da compra no cabeçalho de cada título.
        entrada_ids = [
            t.documento_id for t in titulos
            if t.documento_tipo == 'entrada_nf' and t.documento_id
        ]
        entradas = {}
        if entrada_ids:
            entradas = {
                e.pk: e for e in EntradaNF.objects.filter(pk__in=entrada_ids)
            }

        grupos: dict = {}
        for t in titulos:
            chave = (t.tipo_lancamento, t.funcionario_id or t.fornecedor_id or 0)
            g = grupos.get(chave)
            if g is None:
                g = {
                    'fornecedor': t.fornecedor,
                    'beneficiario_nome': t.beneficiario_nome,
                    'beneficiario_documento': t.beneficiario_documento,
                    'titulos': [],
                    'total_saldo': Decimal('0'),
                    'total_valor': Decimal('0'),
                }
                grupos[chave] = g

            entrada = entradas.get(t.documento_id) if t.documento_tipo == 'entrada_nf' else None
            g['titulos'].append({'titulo': t, 'entrada': entrada})
            g['total_saldo'] += t.valor_saldo or Decimal('0')
            g['total_valor'] += t.valor_final or Decimal('0')

        fornecedores = sorted(
            grupos.values(),
            key=lambda x: x['beneficiario_nome'].lower(),
        )

        total_geral_saldo = sum((g['total_saldo'] for g in fornecedores), Decimal('0'))
        total_geral_valor = sum((g['total_valor'] for g in fornecedores), Decimal('0'))

        status_label = dict(STATUS_CHOICES).get(status) if status else 'Em aberto'

        return render(request, 'financeiro/pagar/relatorio.html', {
            'title': 'Relatório de Contas a Pagar',
            'fornecedores': fornecedores,
            'filial': filial,
            'q': q,
            'status_label': status_label,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'total_geral_saldo': total_geral_saldo,
            'total_geral_valor': total_geral_valor,
            'total_titulos': len(titulos),
            'gerado_em': timezone.localtime(),
            **categoria_contexto,
        })


class ContaPagarCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def _context(self, request, form):
        cadastrando_pago = request.GET.get('quitar') == '1'
        return {
            'title': 'Nova Conta Paga' if cadastrando_pago else 'Nova Conta a Pagar',
            'form': form,
            'cancel_url': reverse(
                'financeiro:pagar_pagas' if cadastrando_pago else 'financeiro:pagar_list'
            ),
            'pode_criar_fornecedor': request.user.tem_permissao('cadastros', 'criar'),
        }

    def get(self, request):
        filial = _filial(request)
        form = ContaPagarForm(
            filial=filial,
            initial={
                'quitar_ao_lancar': request.GET.get('quitar') == '1',
                'data_pagamento_imediato': timezone.localdate(),
            },
        )
        return render(request, 'financeiro/pagar/form.html', self._context(request, form))

    def post(self, request):
        filial = _filial(request)
        form = ContaPagarForm(request.POST, request.FILES, filial=filial)
        if not form.is_valid():
            return render(request, 'financeiro/pagar/form.html', self._context(request, form))

        d = form.cleaned_data
        try:
            datas_relevantes = [
                timezone.localdate(),
                d['data_vencimento'],
                d.get('data_pagamento_imediato'),
            ]
            data_emissao_automatica = min(data for data in datas_relevantes if data)
            dados_conta = dict(
                filial=filial,
                fornecedor=d.get('fornecedor'),
                funcionario=d.get('funcionario'),
                tipo_lancamento=d['tipo_lancamento'],
                valor_original=d['valor_original'],
                data_emissao=data_emissao_automatica,
                documento_numero=d.get('documento_numero', ''),
                nota_fiscal_fornecedor=d.get('nota_fiscal_fornecedor', ''),
                chave_acesso_nfe=d.get('chave_acesso_nfe', ''),
                forma_pagamento_prevista=d.get('forma_pagamento_prevista'),
                plano_contas=d.get('plano_contas'),
                observacao=d.get('observacao', ''),
                ajustar_vencimento_dia_util=d.get('ajustar_vencimento_dia_util', False),
                usuario=request.user,
            )
            if d['recorrente']:
                contas = ContaPagarService.criar_recorrencia(
                    **dados_conta,
                    quantidade=d['quantidade_recorrencias'],
                    frequencia=d['frequencia_recorrencia'],
                    data_vencimento=d['data_vencimento'],
                    data_competencia=d.get('data_competencia'),
                )
                messages.success(request, f'{len(contas)} títulos recorrentes lançados com sucesso.')
            elif d['quitar_ao_lancar']:
                conta = ContaPagarService.criar_e_quitar(
                    **dados_conta,
                    data_vencimento=d['data_vencimento'],
                    data_competencia=d.get('data_competencia'),
                    parcela=d['parcela'],
                    total_parcelas=d['total_parcelas'],
                    data_pagamento=d['data_pagamento_imediato'],
                    forma_pagamento_utilizada=d['forma_pagamento_utilizada'],
                    conta_bancaria_pagamento=d.get('conta_bancaria_pagamento'),
                    comprovante_pagamento=d.get('comprovante_pagamento'),
                )
                messages.success(request, f'Conta a pagar #{conta.pk} lançada e quitada com sucesso.')
            else:
                conta = ContaPagarService.criar(
                    **dados_conta,
                    data_vencimento=d['data_vencimento'],
                    data_competencia=d.get('data_competencia'),
                    parcela=d['parcela'],
                    total_parcelas=d['total_parcelas'],
                )
                messages.success(request, f'Conta a pagar #{conta.pk} lançada com sucesso.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/pagar/form.html', self._context(request, form))

        if d.get('quitar_ao_lancar'):
            return redirect(reverse('financeiro:pagar_pagas'))
        return redirect(reverse('financeiro:pagar_list'))


class ContaPagarNotaFiscalLookupView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def get(self, request):
        from apps.compras.models import EntradaNF

        chave = ''.join(
            caractere for caractere in request.GET.get('chave', '') if caractere.isdigit()
        )
        if len(chave) != 44:
            return JsonResponse(
                {'ok': False, 'erro': 'A chave da NF-e deve ter 44 dígitos.'},
                status=400,
            )

        numero = chave[25:34].lstrip('0') or chave[25:34]
        serie = chave[22:25].lstrip('0') or '1'
        entrada = (
            EntradaNF.objects.for_filial(_filial(request))
            .filter(chave_acesso_nf=chave)
            .select_related('fornecedor')
            .first()
        )
        if not entrada:
            return JsonResponse({
                'ok': True,
                'encontrada': False,
                'chave': chave,
                'numero_nf': numero,
                'serie_nf': serie,
            })

        fornecedor = entrada.fornecedor
        return JsonResponse({
            'ok': True,
            'encontrada': True,
            'chave': chave,
            'numero_nf': entrada.numero_nf or numero,
            'serie_nf': entrada.serie_nf or serie,
            'valor_total': str(entrada.valor_total or ''),
            'fornecedor': {
                'id': fornecedor.pk,
                'label': str(fornecedor),
                'search': ' '.join(filter(None, [
                    fornecedor.razao_social,
                    fornecedor.nome_fantasia,
                    fornecedor.cpf_cnpj,
                ])),
            },
        })


class ContaPagarDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request, pk):
        filial = _filial(request)
        conta = get_object_or_404(
            ContaPagar.objects.for_filial(filial).select_related(
                'fornecedor', 'funcionario', 'forma_pagamento', 'forma_pagamento_prevista', 'conta_bancaria',
                'plano_contas', 'conta_contabil', 'usuario', 'usuario_pagamento',
            ).prefetch_related(
                'pagamentos__forma_pagamento', 'pagamentos__conta_bancaria', 'pagamentos__usuario',
            ),
            pk=pk,
        )
        pode_pagar = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status not in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]
        )
        pode_cancelar = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status not in [StatusContaPagar.CANCELADO, StatusContaPagar.PAGO]
        )

        context = {
            'title': f'Conta a Pagar #{conta.pk}',
            'conta': conta,
            'pode_pagar': pode_pagar,
            'pode_cancelar': pode_cancelar,
            'pill': PILL_STATUS.get(conta.status, 'is-slate'),
            'tipo_conta': 'pagar',
            'pode_editar_valor': _usuario_admin(request) and conta.status != StatusContaPagar.CANCELADO,
            'logs_edicao_valor': _logs_edicao_valor(conta),
        }
        if request.GET.get('modal') == '1':
            return render(request, 'financeiro/_detalhes_conta_modal.html', context)
        return render(request, 'financeiro/pagar/detail.html', context)


class ContaPagarEditarValorView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    @transaction.atomic
    def post(self, request, pk):
        if not _usuario_admin(request):
            return JsonResponse({'ok': False, 'erro': 'Somente administradores podem corrigir valores.'}, status=403)
        conta = get_object_or_404(ContaPagar.objects.for_filial(_filial(request)), pk=pk)
        motivo = request.POST.get('motivo', '').strip()
        valor_texto = request.POST.get('novo_valor', '').strip()
        if not motivo:
            return JsonResponse({'ok': False, 'erro': 'Informe o motivo da alteracao.'}, status=400)
        try:
            normalizado = valor_texto.replace('.', '').replace(',', '.') if ',' in valor_texto else valor_texto
            novo_valor = Decimal(normalizado)
        except Exception:
            return JsonResponse({'ok': False, 'erro': 'Informe um valor valido.'}, status=400)

        antes = snapshot_modelo(conta, campos=[
            'valor_original', 'valor_final', 'valor_pago', 'valor_saldo', 'status',
        ])
        try:
            conta, pagamento = ContaPagarService.corrigir_valor(conta, novo_valor)
        except DomainError as exc:
            return JsonResponse({'ok': False, 'erro': str(exc)}, status=400)
        depois = snapshot_modelo(conta, campos=[
            'valor_original', 'valor_final', 'valor_pago', 'valor_saldo', 'status',
        ])
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.AJUSTAR,
            objeto=conta,
            relacionado=pagamento,
            descricao=f'Valor da conta a pagar #{conta.pk} corrigido',
            justificativa=motivo,
            antes=antes,
            depois=depois,
            metadados={
                'campo': 'valor_original',
                'pagamento_ajustado_id': pagamento.pk if pagamento else None,
                'contas_envolvidas': [pagamento.conta_bancaria_id] if pagamento and pagamento.conta_bancaria_id else [],
            },
        )
        return JsonResponse({
            'ok': True,
            'mensagem': 'Valor corrigido e registrado no log.',
            'valor_original': f'{conta.valor_original:.2f}',
            'valor_final': f'{conta.valor_final:.2f}',
            'valor_pago': f'{conta.valor_pago:.2f}',
            'valor_saldo': f'{conta.valor_saldo:.2f}',
            'status': conta.get_status_display(),
        })


class ContaPagarPagamentoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def _get_conta(self, request, pk):
        return get_object_or_404(
            ContaPagar.objects.for_filial(_filial(request)).select_related(
                'fornecedor', 'funcionario', 'forma_pagamento_prevista', 'forma_pagamento',
                'plano_contas__conta_pai__conta_pai',
            ),
            pk=pk,
        )

    def get(self, request, pk):
        conta = self._get_conta(request, pk)
        if conta.status in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]:
            messages.warning(request, 'Esta conta não pode ser paga.')
            return redirect(reverse('financeiro:pagar_detail', args=[pk]))

        form = PagamentoContaPagarForm(filial=_filial(request), conta=conta)
        return render(request, 'financeiro/pagar/pagamento.html', {
            'title': f'Pagar — #{conta.pk}',
            'conta': conta,
            'form': form,
            'cancel_url': reverse('financeiro:pagar_detail', args=[pk]),
        })

    def post(self, request, pk):
        conta = self._get_conta(request, pk)
        if conta.status in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]:
            messages.warning(request, 'Esta conta não pode ser paga.')
            return redirect(reverse('financeiro:pagar_detail', args=[pk]))

        form = PagamentoContaPagarForm(
            request.POST, request.FILES, filial=_filial(request), conta=conta,
        )
        if not form.is_valid():
            return render(request, 'financeiro/pagar/pagamento.html', {
                'title': f'Pagar — #{conta.pk}',
                'conta': conta,
                'form': form,
                'cancel_url': reverse('financeiro:pagar_detail', args=[pk]),
            })

        d = form.cleaned_data
        try:
            ContaPagarService.registrar_pagamento(
                conta=conta,
                data_pagamento=d['data_pagamento'],
                valor_pago=d['valor_pago'],
                forma_pagamento=d['forma_pagamento'],
                usuario=request.user,
                conta_bancaria=d.get('conta_bancaria'),
                valor_juros=d.get('valor_juros'),
                valor_multa=d.get('valor_multa'),
                valor_desconto=d.get('valor_desconto'),
                referencia_pagamento=d.get('referencia_pagamento', ''),
                comprovante=d.get('comprovante'),
                observacao=d.get('observacao', ''),
            )
            if conta.status == StatusContaPagar.PAGO:
                messages.success(request, f'Conta #{pk} paga integralmente. ✓')
            else:
                messages.success(request, f'Pagamento parcial registrado. Saldo restante: R$ {conta.valor_saldo:,.2f}.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/pagar/pagamento.html', {
                'title': f'Pagar — #{conta.pk}',
                'conta': conta,
                'form': form,
                'cancel_url': reverse('financeiro:pagar_detail', args=[pk]),
            })

        return redirect(reverse('financeiro:pagar_detail', args=[pk]))


class ComprovantePagamentoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request, pk, pagamento_pk):
        pagamento = get_object_or_404(
            PagamentoContaPagar.objects.for_filial(_filial(request)),
            pk=pagamento_pk,
            conta_pagar_id=pk,
        )
        if not pagamento.comprovante_arquivo:
            raise Http404('Comprovante não encontrado.')

        nome = Path(
            pagamento.comprovante_nome_original or pagamento.comprovante_arquivo.name
        ).name
        tipo, _ = mimetypes.guess_type(nome)
        try:
            arquivo = pagamento.comprovante_arquivo.open('rb')
        except (FileNotFoundError, OSError):
            raise Http404('Arquivo do comprovante não encontrado.')
        return FileResponse(
            arquivo,
            as_attachment=request.GET.get('download') == '1',
            filename=nome,
            content_type=tipo or 'application/octet-stream',
        )


class ContaPagarCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        conta = get_object_or_404(
            ContaPagar.objects.for_filial(_filial(request)), pk=pk
        )
        motivo = request.POST.get('motivo', '').strip() or 'Cancelado pelo usuário.'
        try:
            ContaPagarService.cancelar(conta, motivo, request.user)
            messages.success(request, f'Conta #{pk} cancelada.')
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('financeiro:pagar_detail', args=[pk]))
