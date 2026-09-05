"""Views de Contas a Pagar."""
from __future__ import annotations

from calendar import monthrange
from datetime import timedelta
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
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import RegistroAuditoria
from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.forms.pagar import (
    ContaPagarBulkEditForm,
    ContaPagarBulkPagamentoForm,
    ContaPagarEdicaoAdminForm,
    ContaPagarForm,
    DespesaPagaForm,
    MetaDespesaPessoalForm,
    PagamentoContaPagarForm,
)
from apps.financeiro.models.conta_bancaria import ContaBancaria, PlanoContas
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber, MetaDespesaPessoal, PagamentoContaPagar
from apps.financeiro.services.pagar_service import ContaPagarService
from apps.financeiro.services.dashboard_contas_service import DashboardContasService

STATUS_CHOICES = StatusContaPagar.choices

PILL_STATUS = {
    StatusContaPagar.ABERTO:    'is-blue',
    StatusContaPagar.PAGO_PARCIAL: 'is-amber',
    StatusContaPagar.PAGO:      'is-green',
    StatusContaPagar.VENCIDO:   'is-red',
    StatusContaPagar.CANCELADO: 'is-slate',
    StatusContaPagar.AGENDADO:  'is-amber',
}


def _filial(request):
    return request.filial_ativa


def _usuario_admin(request):
    perfil = getattr(request.user, 'perfil', None)
    return bool(getattr(request.user, 'is_superuser', False) or (perfil and perfil.is_admin))


CAMPOS_EDICAO_LANCAMENTO = {
    'descricao_despesa': 'Descrição da despesa',
    'fornecedor': 'Fornecedor',
    'valor_original': 'Valor do titulo',
    'valor_final': 'Valor final',
    'valor_pago': 'Valor pago',
    'valor_saldo': 'Saldo',
    'data_emissao': 'Emissão',
    'data_vencimento': 'Vencimento',
    'data_competencia': 'Competencia',
    'forma_pagamento_prevista': 'Forma prevista',
    'plano_contas': 'Categoria financeira',
    'conta_contabil': 'Conta contabil automatica',
    'data_pagamento': 'Data do pagamento',
    'forma_pagamento': 'Forma utilizada',
    'conta_bancaria': 'Conta bancaria',
    'observacao': 'Observacao',
    'status': 'Status',
    'excluido_em': 'Excluído em',
    'excluido_por': 'Excluído por',
    'motivo_exclusao': 'Motivo da exclusão',
}


def _nome_objeto(objeto):
    if not objeto:
        return 'Nao informado'
    return getattr(objeto, 'descricao', None) or str(objeto)


def _snapshot_edicao_lancamento(conta, pagamento=None):
    pagamento = pagamento or conta.pagamentos.order_by(
        '-data_pagamento', '-created_at', '-pk',
    ).first()
    return {
        'descricao_despesa': conta.descricao_exibicao,
        'fornecedor': _nome_objeto(conta.fornecedor),
        'valor_original': str(conta.valor_original),
        'valor_final': str(conta.valor_final),
        'valor_pago': str(conta.valor_pago),
        'valor_saldo': str(conta.valor_saldo),
        'data_emissao': conta.data_emissao.isoformat(),
        'data_vencimento': conta.data_vencimento.isoformat(),
        'data_competencia': conta.data_competencia.isoformat() if conta.data_competencia else 'Nao informado',
        'forma_pagamento_prevista': _nome_objeto(conta.forma_pagamento_prevista),
        'plano_contas': conta.plano_contas.caminho_descricao if conta.plano_contas else 'Nao informado',
        'conta_contabil': (
            f'{conta.conta_contabil.classificacao} - {conta.conta_contabil.descricao}'
            if conta.conta_contabil else 'Nao informado'
        ),
        'data_pagamento': pagamento.data_pagamento.isoformat() if pagamento else 'Nao informado',
        'forma_pagamento': _nome_objeto(pagamento.forma_pagamento if pagamento else conta.forma_pagamento),
        'conta_bancaria': _nome_objeto(pagamento.conta_bancaria if pagamento else conta.conta_bancaria),
        'observacao': conta.observacao or 'Nao informado',
        'status': conta.get_status_display(),
    }


def _logs_edicao_lancamento(conta):
    logs = list(RegistroAuditoria.objects.filter(
        objeto_tipo=conta._meta.label_lower,
        objeto_id=conta.pk,
        modulo=RegistroAuditoria.Modulo.FINANCEIRO,
    ).select_related('usuario')[:50])
    for log in logs:
        anteriores = log.dados_anteriores or {}
        novos = log.dados_novos or {}
        campos_log = [
            campo for campo in CAMPOS_EDICAO_LANCAMENTO
            if campo in anteriores or campo in novos
        ]
        log.titulo_amigavel = {
            RegistroAuditoria.Acao.CRIAR: 'Conta registrada',
            RegistroAuditoria.Acao.AJUSTAR: 'Lancamento corrigido',
            RegistroAuditoria.Acao.EXCLUIR: 'Conta excluida',
            RegistroAuditoria.Acao.RESTAURAR: 'Conta restaurada',
        }.get(log.acao, log.get_acao_display())
        log.alteracoes_exibicao = [
            {
                'campo': CAMPOS_EDICAO_LANCAMENTO.get(campo, campo.replace('_', ' ').title()),
                'antes': anteriores.get(campo, 'Nao informado'),
                'depois': novos.get(campo, 'Nao informado'),
            }
            for campo in campos_log
            if anteriores.get(campo) != novos.get(campo)
            and log.acao != RegistroAuditoria.Acao.CRIAR
        ]
        log.quantidade_alteracoes = len(log.alteracoes_exibicao)
    return logs


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


def _filtrar_contas_pagar_abertas(request, manager=None, params=None):
    filial = _filial(request)
    params = params or request.GET
    manager = manager or ContaPagar.objects
    qs = (
        manager.for_filial(filial)
        .select_related('fornecedor', 'funcionario', 'forma_pagamento', 'forma_pagamento_prevista', 'plano_contas')
        .order_by('data_vencimento')
    )
    status = params.get('status', 'pendentes')
    q = params.get('q', '').strip()
    beneficiario = params.get('beneficiario', '').strip()
    fornecedor_id = params.get('fornecedor', '').strip()
    funcionario_id = params.get('funcionario', '').strip()
    data_ini = params.get('data_ini', '')
    data_fim = params.get('data_fim', '')
    if not params and status == 'pendentes':
        data_fim = timezone.localdate().isoformat()

    categoria_contexto = _categorias_financeiras_filtro(request)
    if params is not request.GET:
        original_get = request.GET
        request.GET = params
        try:
            categoria_contexto = _categorias_financeiras_filtro(request)
        finally:
            request.GET = original_get

    if not status or status == 'pendentes':
        qs = qs.filter(status__in=(
            StatusContaPagar.ABERTO,
            StatusContaPagar.PAGO_PARCIAL,
            StatusContaPagar.VENCIDO,
            StatusContaPagar.AGENDADO,
        ))
    elif status and status != 'todos':
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(
            Q(descricao_despesa__icontains=q)
            | Q(fornecedor__razao_social__icontains=q)
            | Q(fornecedor__nome_fantasia__icontains=q)
            | Q(funcionario__nome__icontains=q)
            | Q(funcionario__cpf__icontains=q)
            | Q(documento_numero__icontains=q)
            | Q(nota_fiscal_fornecedor__icontains=q)
        )
    if beneficiario:
        qs = qs.filter(
            Q(fornecedor__razao_social__icontains=beneficiario)
            | Q(fornecedor__nome_fantasia__icontains=beneficiario)
            | Q(fornecedor__cpf_cnpj__icontains=beneficiario)
            | Q(funcionario__nome__icontains=beneficiario)
            | Q(funcionario__cpf__icontains=beneficiario)
        )
    if fornecedor_id.isdigit():
        qs = qs.filter(fornecedor_id=int(fornecedor_id))
    if funcionario_id.isdigit():
        qs = qs.filter(funcionario_id=int(funcionario_id))
    if data_ini:
        qs = qs.filter(data_vencimento__gte=data_ini)
    if data_fim:
        qs = qs.filter(data_vencimento__lte=data_fim)
    qs = _aplicar_filtro_categoria_financeira(qs, categoria_contexto)
    return qs, categoria_contexto, {
        'status': status,
        'q': q,
        'beneficiario': beneficiario,
        'fornecedor': fornecedor_id if fornecedor_id.isdigit() else '',
        'funcionario': funcionario_id if funcionario_id.isdigit() else '',
        'data_ini': data_ini,
        'data_fim': data_fim,
    }


def _periodos_datas_contas_pagar(request, data_ini, data_fim):
    hoje = timezone.localdate()
    ontem = hoje - timedelta(days=1)
    inicio_mes = hoje.replace(day=1)
    fim_mes = hoje.replace(day=monthrange(hoje.year, hoje.month)[1])
    opcoes = [
        ('hoje', 'Hoje', hoje, hoje),
        ('ontem', 'Ontem', ontem, ontem),
        ('7_dias', '7 dias', hoje, hoje + timedelta(days=7)),
        ('15_dias', '15 dias', hoje, hoje + timedelta(days=15)),
        ('este_mes', 'Este mês', inicio_mes, fim_mes),
        ('30_dias', '30 dias', hoje, hoje + timedelta(days=30)),
        ('6_meses', '6 meses', hoje, hoje + timedelta(days=182)),
        ('1_ano', '1 ano', hoje, hoje + timedelta(days=365)),
    ]
    periodos = []
    for slug, label, inicio, fim in opcoes:
        qd = request.GET.copy()
        qd.pop('page', None)
        qd['data_ini'] = inicio.isoformat()
        qd['data_fim'] = fim.isoformat()
        periodos.append({
            'slug': slug,
            'label': label,
            'url': f"{reverse('financeiro:pagar_list')}?{qd.urlencode()}",
            'active': data_ini == inicio.isoformat() and data_fim == fim.isoformat(),
        })
    return periodos


def _kpis(qs_base):
    hoje = timezone.localdate()
    primeiro_dia_mes = hoje.replace(day=1)

    totais = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.PAGO_PARCIAL, StatusContaPagar.VENCIDO, StatusContaPagar.AGENDADO]
    ).aggregate(
        total_aberto=Sum('valor_saldo'),
        qtd_aberto=Count('id'),
    )

    vencido = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.PAGO_PARCIAL, StatusContaPagar.VENCIDO],
        data_vencimento__lt=hoje,
    ).aggregate(total_vencido=Sum('valor_saldo'))

    pago_mes = qs_base.filter(
        status=StatusContaPagar.PAGO,
        data_pagamento__gte=primeiro_dia_mes,
    ).aggregate(total_mes=Sum('valor_pago'))

    vence_hoje = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.PAGO_PARCIAL, StatusContaPagar.VENCIDO],
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

        mostrar_excluidos = request.GET.get('mostrar_excluidos') == '1' and _usuario_admin(request)
        manager = ContaPagar.all_objects if mostrar_excluidos else ContaPagar.objects

        kpis = _kpis(ContaPagar.objects.for_filial(filial))
        qs, categoria_contexto, filtros = _filtrar_contas_pagar_abertas(request, manager)

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
            'status_filtro': filtros['status'],
            'q': filtros['q'],
            'beneficiario_filtro': filtros['beneficiario'],
            'data_ini': filtros['data_ini'],
            'data_fim': filtros['data_fim'],
            'periodos_datas': _periodos_datas_contas_pagar(request, filtros['data_ini'], filtros['data_fim']),
            'totais_filtro': totais_filtro,
            'page_querystring': page_querystring,
            'pill_status': PILL_STATUS,
            'pode_criar': pode_criar,
            'pode_editar': pode_editar,
            'user_is_admin': _usuario_admin(request),
            'mostrar_excluidos': mostrar_excluidos,
            'today': timezone.localdate(),
            'dashboard_contas': DashboardContasService.apurar(filial),
            'bulk_edit_form': ContaPagarBulkEditForm(filial=filial),
            'bulk_pagamento_form': ContaPagarBulkPagamentoForm(filial=filial),
            **categoria_contexto,
            **kpis,
        })


def _filtrar_contas_pagas(request):
    """Retorna o historico de contas pagas da filial com os filtros da tela."""
    mostrar_excluidos = request.GET.get('mostrar_excluidos') == '1' and _usuario_admin(request)
    manager = ContaPagar.all_objects if mostrar_excluidos else ContaPagar.objects
    qs = (
        manager.for_filial(_filial(request))
        .filter(status=StatusContaPagar.PAGO)
        .select_related(
            'fornecedor', 'funcionario', 'forma_pagamento', 'conta_bancaria',
            'plano_contas', 'conta_contabil',
        )
    )

    q = request.GET.get('q', '').strip()
    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)
    fim_mes = hoje.replace(day=monthrange(hoje.year, hoje.month)[1])
    data_ini = request.GET.get('data_ini') or inicio_mes.isoformat()
    data_fim = request.GET.get('data_fim') or fim_mes.isoformat()
    ordenacao = request.GET.get('ordenacao', 'recentes')
    fornecedor_id = request.GET.get('fornecedor', '').strip()
    funcionario_id = request.GET.get('funcionario', '').strip()
    categoria_contexto = _categorias_financeiras_filtro(request)

    if q:
        qs = qs.filter(
            Q(descricao_despesa__icontains=q)
            | Q(fornecedor__razao_social__icontains=q)
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
    if fornecedor_id.isdigit():
        qs = qs.filter(fornecedor_id=int(fornecedor_id))
    if funcionario_id.isdigit():
        qs = qs.filter(funcionario_id=int(funcionario_id))
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
        'fornecedor': fornecedor_id if fornecedor_id.isdigit() else '',
        'funcionario': funcionario_id if funcionario_id.isdigit() else '',
        'mostrar_excluidos': mostrar_excluidos,
        **categoria_contexto,
    }


def _limites_mes(referencia):
    inicio = referencia.replace(day=1)
    fim = referencia.replace(day=monthrange(referencia.year, referencia.month)[1])
    return inicio, fim


def _somar_faturamento(filial, inicio, fim):
    total = ContaReceber.objects.for_filial(filial).filter(
        status='pago',
        data_pagamento__range=(inicio, fim),
    ).aggregate(total=Sum('valor_pago'))['total'] or Decimal('0')
    try:
        from apps.pdv.models import PagamentoVendaPDV
    except ImportError:
        PagamentoVendaPDV = None
    if PagamentoVendaPDV:
        total += PagamentoVendaPDV.objects.filter(
            venda_pdv__filial=filial,
            venda_pdv__cancelado_em__isnull=True,
            data_liquidacao_prevista__range=(inicio, fim),
        ).exclude(
            venda_pdv__status='cancelado',
        ).aggregate(total=Sum('valor_liquido'))['total'] or Decimal('0')
    return total


def _mes_anterior(referencia):
    primeiro = referencia.replace(day=1)
    ultimo_mes_anterior = primeiro - timedelta(days=1)
    return _limites_mes(ultimo_mes_anterior)


def _valor_meta_despesa_pessoal(meta, filial, referencia):
    if not meta or not meta.ativo:
        return Decimal('0')
    if meta.tipo_meta == MetaDespesaPessoal.TipoMeta.VALOR_FIXO:
        return meta.valor_fixo or Decimal('0')
    percentual = (meta.percentual or Decimal('0')) / Decimal('100')
    if percentual <= 0:
        return Decimal('0')
    if meta.tipo_meta == MetaDespesaPessoal.TipoMeta.PERCENTUAL_MES_ANTERIOR:
        inicio, fim = _mes_anterior(referencia)
        return (_somar_faturamento(filial, inicio, fim) * percentual).quantize(Decimal('0.01'))

    meses = max(2, min(int(meta.meses_media or 3), 24))
    primeiro_mes_atual = referencia.replace(day=1)
    cursor = primeiro_mes_atual
    total = Decimal('0')
    for _ in range(meses):
        cursor = cursor - timedelta(days=1)
        inicio, fim = _limites_mes(cursor)
        total += _somar_faturamento(filial, inicio, fim)
        cursor = inicio
    return ((total / Decimal(meses)) * percentual).quantize(Decimal('0.01'))


def _resumo_fornecedores(contas):
    total = sum((conta.valor_pago or Decimal('0') for conta in contas), Decimal('0'))
    grupos = {}
    for conta in contas:
        nome = conta.beneficiario_nome
        grupo = grupos.setdefault(nome, {
            'nome': nome, 'valor': Decimal('0'), 'quantidade': 0, 'contas': [],
        })
        grupo['valor'] += conta.valor_pago or Decimal('0')
        grupo['quantidade'] += 1
        grupo['contas'].append(conta)
    resumo = []
    for grupo in grupos.values():
        percentual = (grupo['valor'] / total * Decimal('100')) if total else Decimal('0')
        resumo.append({**grupo, 'percentual': percentual})
    return sorted(resumo, key=lambda item: item['valor'], reverse=True), total


def _resumo_categorias_pagas(contas, faturamento=Decimal('0')):
    total = sum((conta.valor_pago or Decimal('0') for conta in contas), Decimal('0'))
    grupos = {}
    sem_classificacao = Decimal('0')
    for conta in contas:
        valor = conta.valor_pago or Decimal('0')
        categoria = conta.plano_contas
        if not categoria:
            sem_classificacao += valor
            continue
        grupo = categoria
        while grupo.conta_pai_id and grupo.conta_pai:
            grupo = grupo.conta_pai
        item = grupos.setdefault(grupo.pk, {
            'nome': grupo.descricao, 'valor': Decimal('0'), 'quantidade': 0,
        })
        item['valor'] += valor
        item['quantidade'] += 1
    resumo = []
    for item in grupos.values():
        percentual = (item['valor'] / total * Decimal('100')) if total else Decimal('0')
        impacto = (item['valor'] / faturamento * Decimal('100')) if faturamento else Decimal('0')
        resumo.append({**item, 'percentual': percentual, 'impacto_faturamento': impacto})
    resumo.sort(key=lambda item: item['valor'], reverse=True)
    maior = resumo[0] if resumo else None
    return {
        'categorias_resumo': resumo,
        'categorias_total': total,
        'categorias_quantidade': len(resumo),
        'categoria_maior': maior,
        'categorias_sem_classificacao': sem_classificacao,
        'categorias_sem_classificacao_percentual': (
            sem_classificacao / total * Decimal('100') if total else Decimal('0')
        ),
        'categorias_faturamento': faturamento,
    }


def _periodo_fornecedores(request):
    hoje = timezone.localdate()
    periodo = request.GET.get('fornecedor_periodo', 'mes')
    if periodo == 'hoje':
        inicio = fim = hoje
    elif periodo == '7':
        inicio, fim = hoje - timedelta(days=6), hoje
    elif periodo == '15':
        inicio, fim = hoje - timedelta(days=14), hoje
    elif periodo == '30':
        inicio, fim = hoje - timedelta(days=29), hoje
    elif periodo == 'todos':
        inicio = fim = None
    elif periodo == 'personalizado':
        inicio = parse_date(request.GET.get('fornecedor_inicio', '')) or hoje.replace(day=1)
        fim = parse_date(request.GET.get('fornecedor_fim', '')) or hoje
        if inicio > fim:
            inicio, fim = fim, inicio
    else:
        periodo = 'mes'
        inicio, fim = hoje.replace(day=1), hoje
    return periodo, inicio, fim


def _contexto_meta_despesa_pessoal(filial, referencia=None):
    hoje = referencia or timezone.localdate()
    inicio_mes, fim_mes = _limites_mes(hoje)
    meta = MetaDespesaPessoal.objects.filter(filial=filial).first()
    usado = ContaPagar.objects.for_filial(filial).filter(
        status=StatusContaPagar.PAGO,
        data_pagamento__range=(inicio_mes, fim_mes),
        plano_contas__despesa_pessoal=True,
    ).aggregate(total=Sum('valor_pago'))['total'] or Decimal('0')
    valor_meta = _valor_meta_despesa_pessoal(meta, filial, hoje)
    percentual = (usado / valor_meta * Decimal('100')) if valor_meta > 0 else Decimal('0')
    disponivel = max(valor_meta - usado, Decimal('0'))
    return {
        'meta_despesa_pessoal': meta,
        'meta_despesa_form': MetaDespesaPessoalForm(instance=meta),
        'meta_despesa_valor': valor_meta,
        'meta_despesa_usado': usado,
        'meta_despesa_percentual': percentual,
        'meta_despesa_percentual_barra': min(percentual, Decimal('100')),
        'meta_despesa_disponivel': disponivel,
        'meta_despesa_mes': hoje,
    }


class ContaPagaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def post(self, request):
        if request.POST.get('acao') != 'salvar_meta_despesa_pessoal':
            return redirect(reverse('financeiro:pagar_pagas'))
        if not _usuario_admin(request):
            messages.error(request, 'Apenas administradores podem configurar a meta.')
            return redirect(reverse('financeiro:pagar_pagas'))
        filial = _filial(request)
        meta = MetaDespesaPessoal.objects.filter(filial=filial).first()
        form = MetaDespesaPessoalForm(request.POST, instance=meta)
        if form.is_valid():
            meta = form.save(commit=False)
            meta.filial = filial
            meta.save()
            messages.success(request, 'Meta de despesas pessoais salva.')
            return redirect(reverse('financeiro:pagar_pagas'))

        qs, filtros = _filtrar_contas_pagas(request)
        return self._render(request, qs, filtros, meta_form=form)

    def get(self, request):
        qs, filtros = _filtrar_contas_pagas(request)
        return self._render(request, qs, filtros)

    def _render(self, request, qs, filtros, meta_form=None):
        filial = _filial(request)
        totais = qs.aggregate(
            quantidade=Count('id'),
            valor_original=Sum('valor_original'),
            valor_pago=Sum('valor_pago'),
            juros=Sum('valor_juros'),
            multas=Sum('valor_multa'),
            descontos=Sum('valor_desconto'),
            despesas_pessoais=Sum('valor_final', filter=Q(plano_contas__despesa_pessoal=True)),
        )
        totais['acrescimos'] = (totais['juros'] or 0) + (totais['multas'] or 0)
        contas_filtradas = list(qs.select_related(
            'plano_contas__conta_pai__conta_pai',
        ))
        datas = [conta.data_pagamento for conta in contas_filtradas if conta.data_pagamento]
        inicio_analise = parse_date(filtros.get('data_ini') or '') or (min(datas) if datas else timezone.localdate())
        fim_analise = parse_date(filtros.get('data_fim') or '') or (max(datas) if datas else timezone.localdate())
        faturamento = _somar_faturamento(filial, inicio_analise, fim_analise)
        categorias_contexto = _resumo_categorias_pagas(contas_filtradas, faturamento)

        fornecedor_periodo, fornecedor_inicio, fornecedor_fim = _periodo_fornecedores(request)
        fornecedor_qs = ContaPagar.objects.for_filial(filial).filter(status=StatusContaPagar.PAGO)
        fornecedor_qs = _aplicar_filtro_categoria_financeira(fornecedor_qs, filtros)
        busca_fornecedor = (filtros.get('q') or '').strip()
        if busca_fornecedor:
            fornecedor_qs = fornecedor_qs.filter(
                Q(descricao_despesa__icontains=busca_fornecedor)
                | Q(fornecedor__razao_social__icontains=busca_fornecedor)
                | Q(fornecedor__nome_fantasia__icontains=busca_fornecedor)
                | Q(funcionario__nome__icontains=busca_fornecedor)
                | Q(documento_numero__icontains=busca_fornecedor)
                | Q(nota_fiscal_fornecedor__icontains=busca_fornecedor)
                | Q(pagamentos__referencia_pagamento__icontains=busca_fornecedor)
            ).distinct()
        if fornecedor_inicio:
            fornecedor_qs = fornecedor_qs.filter(data_pagamento__gte=fornecedor_inicio)
        if fornecedor_fim:
            fornecedor_qs = fornecedor_qs.filter(data_pagamento__lte=fornecedor_fim)
        contas_fornecedores = list(fornecedor_qs.select_related(
            'fornecedor', 'funcionario', 'plano_contas',
        ).order_by('-data_pagamento', '-id'))
        fornecedores_resumo, total_fornecedores = _resumo_fornecedores(contas_fornecedores)
        paginator = Paginator(contas_filtradas, 10)
        page_obj = paginator.get_page(request.GET.get('page', 1))
        query = request.GET.copy()
        query.pop('page', None)
        meta_contexto = _contexto_meta_despesa_pessoal(filial)
        if meta_form is not None:
            meta_contexto['meta_despesa_form'] = meta_form

        return render(request, 'financeiro/pagar/pagas.html', {
            'title': 'Contas Pagas',
            'contas': page_obj,
            'page_obj': page_obj,
            'totais': totais,
            'page_querystring': query.urlencode(),
            'pode_criar': request.user.tem_permissao('financeiro', 'criar'),
            'user_is_admin': _usuario_admin(request),
            'fornecedores_resumo': fornecedores_resumo,
            'total_fornecedores': total_fornecedores,
            'fornecedor_periodo': fornecedor_periodo,
            'fornecedor_inicio': fornecedor_inicio,
            'fornecedor_fim': fornecedor_fim,
            'analise_inicio': inicio_analise,
            'analise_fim': fim_analise,
            **categorias_contexto,
            **meta_contexto,
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
            despesas_pessoais=Sum('valor_final', filter=Q(plano_contas__despesa_pessoal=True)),
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
    """Impressão tabular da mesma seleção exibida em Contas a Pagar."""

    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        filial = _filial(request)
        ContaPagarService.atualizar_status_vencidos(filial)
        qs, categoria_contexto, filtros = _filtrar_contas_pagar_abertas(request)
        totais = qs.aggregate(
            total_valor=Sum('valor_final'),
            total_pago=Sum('valor_pago'),
            total_saldo=Sum('valor_saldo'),
        )
        limite = 2000
        total_encontrado = qs.count()
        titulos = list(qs[:limite])
        status_label = {
            **dict(STATUS_CHOICES),
            'todos': 'Todos os status',
            'pendentes': 'Somente pendentes',
            '': 'Somente pendentes',
        }.get(filtros['status'], filtros['status'])
        categoria_label = ''
        if categoria_contexto['categoria_financeira_selecionada']:
            categoria_label = categoria_contexto['categoria_financeira_selecionada'].descricao
        elif categoria_contexto['categoria_subgrupo_filtro']:
            categoria_label = next((
                item.descricao for item in categoria_contexto['categoria_subgrupos']
                if str(item.pk) == categoria_contexto['categoria_subgrupo_filtro']
            ), '')
        elif categoria_contexto['categoria_grupo_filtro']:
            categoria_label = next((
                item.descricao for item in categoria_contexto['categoria_grupos']
                if str(item.pk) == categoria_contexto['categoria_grupo_filtro']
            ), '')

        return render(request, 'financeiro/pagar/relatorio.html', {
            'title': 'Relatório de Contas a Pagar',
            'titulos': titulos,
            'filial': filial,
            'q': filtros['q'],
            'beneficiario_filtro': filtros['beneficiario'],
            'categoria_label': categoria_label,
            'status_label': status_label,
            'data_ini': filtros['data_ini'],
            'data_fim': filtros['data_fim'],
            'total_geral_saldo': totais['total_saldo'] or Decimal('0'),
            'total_geral_valor': totais['total_valor'] or Decimal('0'),
            'total_geral_pago': totais['total_pago'] or Decimal('0'),
            'total_titulos': total_encontrado,
            'truncado': total_encontrado > limite,
            'limite': limite,
            'querystring': request.GET.urlencode(),
            'gerado_em': timezone.localtime(),
            **categoria_contexto,
        })


@method_decorator(xframe_options_sameorigin, name='dispatch')
class ContaPagarCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def _context(self, request, form):
        cadastrando_pago = request.GET.get('quitar') == '1'
        return {
            'title': 'Nova Conta Paga' if cadastrando_pago else 'Nova Conta a Pagar',
            'form': form,
            'modal_mode': request.GET.get('modal') == '1',
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
                descricao_despesa=d['descricao_despesa'],
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
                antecipar_vencimento_dia_util=d.get('antecipar_vencimento_dia_util', False),
                usuario=request.user,
            )
            if d['recorrente']:
                contas = ContaPagarService.criar_recorrencia(
                    **dados_conta,
                    quantidade=d['quantidade_recorrencias'],
                    frequencia=d['frequencia_recorrencia'],
                    intervalo_dias=d.get('intervalo_recorrencia_dias'),
                    dias_semana=d.get('dias_semana_recorrencia'),
                    regra_vencimento_mensal=d.get('regra_vencimento_mensal'),
                    dia_vencimento_mensal=d.get('dia_vencimento_mensal'),
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

        if request.GET.get('modal') == '1':
            return render(request, 'financeiro/pagar/modal_success.html')
        if d.get('quitar_ao_lancar'):
            return redirect(f"{reverse('financeiro:pagar_criar')}?quitar=1")
        return redirect(reverse('financeiro:pagar_criar'))


@method_decorator(xframe_options_sameorigin, name='dispatch')
class DespesaPagaCreateView(PermissaoRequiredMixin, View):
    """Cadastro curto de uma despesa que ja nasce integralmente quitada."""

    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def _context(self, request, form):
        return {
            'title': 'Nova despesa paga',
            'form': form,
            'modal_mode': request.GET.get('modal') == '1',
            'cancel_url': reverse('financeiro:pagar_pagas'),
            'pode_criar_fornecedor': request.user.tem_permissao('cadastros', 'criar'),
            'pode_criar_categoria': request.user.tem_permissao('financeiro', 'criar'),
            'categoria_grupos_json': [
                {'id': item.pk, 'descricao': item.descricao, 'codigo': item.codigo}
                for item in form.categoria_grupos
            ],
            'categoria_subgrupos_json': [
                {
                    'id': item.pk, 'descricao': item.descricao,
                    'codigo': item.codigo, 'pai_id': item.conta_pai_id,
                }
                for item in form.categoria_subgrupos
            ],
            'categorias_json': [
                {
                    'id': item.pk, 'descricao': item.descricao,
                    'codigo': item.codigo, 'pai_id': item.conta_pai_id,
                }
                for item in form.fields['plano_contas'].queryset
            ],
            'contas_contabeis_json': [
                {
                    'id': item.pk,
                    'label': f'{item.classificacao} - {item.descricao}',
                }
                for item in form.contas_contabeis
            ],
        }

    def get(self, request):
        form = DespesaPagaForm(filial=_filial(request))
        return render(request, 'financeiro/pagar/despesa_paga_form.html', self._context(request, form))

    def post(self, request):
        filial = _filial(request)
        form = DespesaPagaForm(request.POST, request.FILES, filial=filial)
        if not form.is_valid():
            return render(
                request, 'financeiro/pagar/despesa_paga_form.html',
                self._context(request, form),
            )

        d = form.cleaned_data
        data_pagamento = d['data_pagamento']
        try:
            conta = ContaPagarService.criar_e_quitar(
                filial=filial,
                descricao_despesa=d['descricao_despesa'],
                fornecedor=d.get('fornecedor'),
                funcionario=d.get('funcionario'),
                tipo_lancamento=d['tipo_lancamento'],
                valor_original=d['valor_original'],
                data_emissao=data_pagamento,
                data_vencimento=data_pagamento,
                data_competencia=data_pagamento,
                parcela=1,
                total_parcelas=1,
                forma_pagamento_prevista=d['forma_pagamento_utilizada'],
                plano_contas=d['plano_contas'],
                observacao=d.get('observacao', ''),
                ajustar_vencimento_dia_util=False,
                data_pagamento=data_pagamento,
                forma_pagamento_utilizada=d['forma_pagamento_utilizada'],
                conta_bancaria_pagamento=d.get('conta_bancaria_pagamento'),
                comprovante_pagamento=d.get('comprovante_pagamento'),
                usuario=request.user,
            )
            registrar_auditoria(
                request=request,
                modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                acao=RegistroAuditoria.Acao.CRIAR,
                objeto=conta,
                descricao=f'Despesa paga #{conta.pk} registrada',
                depois=snapshot_modelo(conta),
                metadados={'origem': 'cadastro_despesa_paga'},
            )
        except DomainError as exc:
            form.add_error(None, str(exc))
            return render(
                request, 'financeiro/pagar/despesa_paga_form.html',
                self._context(request, form),
            )

        messages.success(request, f'Despesa paga #{conta.pk} registrada com sucesso.')
        if request.GET.get('modal') == '1':
            return render(request, 'financeiro/pagar/modal_success.html')
        return redirect(reverse('financeiro:pagar_pagas'))


class ContaPagarBulkActionView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def _ids_escopo(self, request):
        escopo = request.POST.get('escopo_lote', 'selecionados')
        if escopo == 'filtro':
            qs, _, _ = _filtrar_contas_pagar_abertas(request, ContaPagar.objects, request.POST)
            return list(qs.values_list('pk', flat=True))
        campo = 'ids_pagina' if escopo == 'pagina' else 'titulo_ids'
        return [
            int(valor)
            for valor in request.POST.getlist(campo)
            if str(valor).isdigit()
        ]

    def _redirect_list(self, request):
        query = request.POST.get('page_querystring', '').strip()
        url = reverse('financeiro:pagar_list')
        return redirect(f'{url}?{query}' if query else url)

    @transaction.atomic
    def post(self, request):
        filial = _filial(request)
        acao = request.POST.get('acao_lote')
        ids = self._ids_escopo(request)
        if not ids:
            messages.error(request, 'Selecione pelo menos um título para a ação em lote.')
            return self._redirect_list(request)

        contas = list(
            ContaPagar.objects.for_filial(filial)
            .select_for_update()
            .filter(pk__in=ids)
            .exclude(status__in=[StatusContaPagar.PAGO, StatusContaPagar.CANCELADO])
            .order_by('data_vencimento', 'pk')
        )
        if not contas:
            messages.warning(request, 'Nenhum título aberto foi encontrado para essa ação.')
            return self._redirect_list(request)

        if acao == 'editar':
            form = ContaPagarBulkEditForm(request.POST, filial=filial)
            if not form.is_valid():
                messages.error(request, ' '.join(m for erros in form.errors.values() for m in erros))
                return self._redirect_list(request)
            d = form.cleaned_data
            for conta in contas:
                antes = _snapshot_edicao_lancamento(conta)
                if d.get('data_vencimento'):
                    conta.data_vencimento = d['data_vencimento']
                    if conta.valor_pago > Decimal('0') and conta.valor_saldo > Decimal('0'):
                        conta.status = StatusContaPagar.PAGO_PARCIAL
                    elif conta.data_vencimento < timezone.localdate():
                        conta.status = StatusContaPagar.VENCIDO
                    else:
                        conta.status = StatusContaPagar.ABERTO
                if d.get('plano_contas'):
                    conta.plano_contas = d['plano_contas']
                    conta.conta_contabil = d['plano_contas'].conta_contabil
                if d.get('forma_pagamento_prevista'):
                    conta.forma_pagamento_prevista = d['forma_pagamento_prevista']
                if d.get('observacao'):
                    sufixo = f'[Edição em lote {timezone.localdate():%d/%m/%Y}] {d["observacao"].strip()}'
                    conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo
                conta.save()
                registrar_auditoria(
                    request=request,
                    modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                    acao=RegistroAuditoria.Acao.AJUSTAR,
                    objeto=conta,
                    descricao=f'Título a pagar #{conta.pk} editado em lote',
                    justificativa='Edição em lote de contas a pagar.',
                    antes=antes,
                    depois=_snapshot_edicao_lancamento(conta),
                    metadados={'origem': 'acao_em_lote'},
                )
            messages.success(request, f'{len(contas)} título(s) editado(s) em lote.')
            return self._redirect_list(request)

        if acao == 'excluir':
            if not _usuario_admin(request):
                messages.error(request, 'Somente administradores podem apagar títulos em lote.')
                return self._redirect_list(request)
            motivo = request.POST.get('motivo_lote', '').strip() or 'Exclusão em lote.'
            for conta in contas:
                antes = snapshot_modelo(conta, ['excluido_em', 'excluido_por', 'motivo_exclusao'])
                conta.excluido_em = timezone.now()
                conta.excluido_por = request.user
                conta.motivo_exclusao = motivo
                conta.save(update_fields=['excluido_em', 'excluido_por', 'motivo_exclusao', 'updated_at'])
                registrar_auditoria(
                    request=request,
                    modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                    acao=RegistroAuditoria.Acao.EXCLUIR,
                    objeto=conta,
                    descricao=f'Título a pagar #{conta.pk} excluído em lote',
                    justificativa=motivo,
                    antes=antes,
                    depois=snapshot_modelo(conta, ['excluido_em', 'excluido_por', 'motivo_exclusao']),
                    metadados={'origem': 'acao_em_lote'},
                )
            messages.success(request, f'{len(contas)} título(s) apagado(s) em lote.')
            return self._redirect_list(request)

        if acao == 'quitar':
            form = ContaPagarBulkPagamentoForm(request.POST, filial=filial)
            if not form.is_valid():
                messages.error(request, ' '.join(m for erros in form.errors.values() for m in erros))
                return self._redirect_list(request)
            d = form.cleaned_data
            quitados = 0
            erros = []
            for conta in contas:
                try:
                    ContaPagarService.registrar_pagamento(
                        conta=conta,
                        data_pagamento=d['data_pagamento'],
                        valor_pago=conta.valor_saldo,
                        forma_pagamento=d['forma_pagamento'],
                        conta_bancaria=d.get('conta_bancaria'),
                        usuario=request.user,
                        observacao=d.get('observacao', '') or 'Quitado em lote.',
                    )
                    quitados += 1
                except DomainError as exc:
                    erros.append(f'#{conta.pk}: {exc}')
            if quitados:
                messages.success(request, f'{quitados} título(s) quitado(s) em lote.')
            if erros:
                messages.warning(request, 'Alguns títulos não foram quitados: ' + '; '.join(erros[:5]))
            return self._redirect_list(request)

        messages.error(request, 'Ação em lote inválida.')
        return self._redirect_list(request)


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
            ContaPagar.all_objects.for_filial(filial).select_related(
                'fornecedor', 'funcionario', 'forma_pagamento', 'forma_pagamento_prevista', 'conta_bancaria',
                'plano_contas', 'conta_contabil', 'usuario', 'usuario_pagamento',
            ).prefetch_related(
                'pagamentos__forma_pagamento', 'pagamentos__conta_bancaria', 'pagamentos__usuario',
            ),
            pk=pk,
        )
        pode_pagar = (
            request.user.tem_permissao('financeiro', 'editar')
            and not conta.excluido
            and conta.status not in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]
        )
        pode_cancelar = (
            request.user.tem_permissao('financeiro', 'editar')
            and not conta.excluido
            and conta.status not in [StatusContaPagar.CANCELADO, StatusContaPagar.PAGO]
        )
        pagamento_selecionado = None
        pagamento_id = request.GET.get('pagamento', '').strip()
        if pagamento_id.isdigit():
            pagamento_selecionado = conta.pagamentos.filter(pk=int(pagamento_id)).first()
        if pagamento_selecionado is None:
            pagamento_selecionado = conta.pagamentos.order_by(
                '-data_pagamento', '-created_at', '-pk',
            ).first()

        pagamentos_detalhados = list(conta.pagamentos.all())
        tarifas_por_pagamento = {
            tarifa.documento_id: tarifa.valor_pago
            for tarifa in ContaPagar.all_objects.for_filial(filial).filter(
                documento_tipo='taxa_pagamento',
                documento_id__in=[pagamento.pk for pagamento in pagamentos_detalhados],
                excluido_em__isnull=True,
            )
        }
        for pagamento in pagamentos_detalhados:
            pagamento.valor_tarifa_bancaria = tarifas_por_pagamento.get(
                pagamento.pk, Decimal('0.00'),
            )
            pagamento.valor_debito_bancario = (
                pagamento.valor_pago + pagamento.valor_tarifa_bancaria
            )

        context = {
            'title': f'Conta a Pagar #{conta.pk}',
            'conta': conta,
            'pode_pagar': pode_pagar,
            'pode_cancelar': pode_cancelar,
            'pill': PILL_STATUS.get(conta.status, 'is-slate'),
            'tipo_conta': 'pagar',
            'pode_editar_lancamento': _usuario_admin(request) and not conta.excluido and conta.status != StatusContaPagar.CANCELADO,
            'user_is_admin': _usuario_admin(request),
            'pagamento_selecionado': pagamento_selecionado,
            'pagamentos_detalhados': pagamentos_detalhados,
            'edicao_lancamento_form': ContaPagarEdicaoAdminForm(
                filial=filial, conta=conta, pagamento=pagamento_selecionado,
            ),
            'logs_edicao_lancamento': _logs_edicao_lancamento(conta),
            'tem_proximos_recorrencia': bool(
                conta.grupo_recorrencia
                and conta.valor_pago == Decimal('0')
                and ContaPagar.objects.for_filial(filial).filter(
                    grupo_recorrencia=conta.grupo_recorrencia,
                    parcela__gt=conta.parcela,
                    valor_pago=Decimal('0'),
                ).exclude(
                    status=StatusContaPagar.CANCELADO,
                ).exists()
            ),
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
            return JsonResponse({'ok': False, 'erro': 'Somente administradores podem editar lancamentos.'}, status=403)
        filial = _filial(request)
        conta = get_object_or_404(
            ContaPagar.all_objects.for_filial(filial).select_related(
                'fornecedor', 'forma_pagamento', 'forma_pagamento_prevista', 'conta_bancaria',
            ),
            pk=pk,
        )
        if conta.excluido:
            return JsonResponse({'ok': False, 'erro': 'Restaure o título antes de editá-lo.'}, status=400)
        if conta.status == StatusContaPagar.CANCELADO:
            return JsonResponse({'ok': False, 'erro': 'Nao e possivel editar uma conta cancelada.'}, status=400)
        pagamento = None
        pagamento_id = request.POST.get('pagamento_id', '').strip()
        if pagamento_id.isdigit():
            pagamento = conta.pagamentos.filter(pk=int(pagamento_id)).first()
            if pagamento is None:
                return JsonResponse({'ok': False, 'erro': 'A baixa selecionada nao pertence a este titulo.'}, status=400)
        form = ContaPagarEdicaoAdminForm(
            request.POST, filial=filial, conta=conta, pagamento=pagamento,
        )
        if not form.is_valid():
            erros = [mensagem for mensagens in form.errors.values() for mensagem in mensagens]
            return JsonResponse({'ok': False, 'erro': ' '.join(erros)}, status=400)

        dados = form.cleaned_data
        pagamento = form.pagamento
        antes = _snapshot_edicao_lancamento(conta, pagamento)
        contas_envolvidas = set()
        if pagamento and pagamento.conta_bancaria_id:
            contas_envolvidas.add(pagamento.conta_bancaria_id)
        try:
            if dados['valor_original'] != conta.valor_original:
                conta, pagamento_ajustado = ContaPagarService.corrigir_valor(
                    conta, dados['valor_original'], pagamento=pagamento,
                )
                pagamento = pagamento_ajustado or pagamento
        except DomainError as exc:
            transaction.set_rollback(True)
            return JsonResponse({'ok': False, 'erro': str(exc)}, status=400)

        if conta.tipo_lancamento == ContaPagar.TipoLancamento.FORNECEDOR:
            conta.fornecedor = dados.get('fornecedor')
        conta.descricao_despesa = dados['descricao_despesa'].strip()
        conta.data_vencimento = dados['data_vencimento']
        conta.data_competencia = dados.get('data_competencia')
        conta.forma_pagamento_prevista = dados.get('forma_pagamento_prevista')
        conta.plano_contas = dados.get('plano_contas')
        conta.conta_contabil = conta.plano_contas.conta_contabil if conta.plano_contas else None
        conta.observacao = dados.get('observacao', '').strip()

        if pagamento:
            pagamento = PagamentoContaPagar.objects.select_for_update().get(pk=pagamento.pk)
            pagamento.data_pagamento = dados['data_pagamento']
            pagamento.forma_pagamento = dados.get('forma_pagamento')
            pagamento.conta_bancaria = dados.get('conta_bancaria')
            pagamento.save(update_fields=[
                'data_pagamento', 'forma_pagamento', 'conta_bancaria', 'updated_at',
            ])
            ultima_baixa = conta.pagamentos.order_by(
                '-data_pagamento', '-created_at', '-pk',
            ).first()
            conta.data_pagamento = ultima_baixa.data_pagamento
            conta.forma_pagamento = ultima_baixa.forma_pagamento
            conta.conta_bancaria = ultima_baixa.conta_bancaria
            if pagamento.data_pagamento < conta.data_emissao:
                conta.data_emissao = pagamento.data_pagamento
            if pagamento.conta_bancaria_id:
                contas_envolvidas.add(pagamento.conta_bancaria_id)

        if conta.status in (StatusContaPagar.ABERTO, StatusContaPagar.PAGO_PARCIAL, StatusContaPagar.VENCIDO):
            conta.status = (
                StatusContaPagar.PAGO_PARCIAL if conta.valor_pago > Decimal('0') and conta.valor_saldo > Decimal('0') else
                StatusContaPagar.VENCIDO
                if conta.data_vencimento < timezone.localdate()
                else StatusContaPagar.ABERTO
            )
        conta.save()
        recorrencia_atualizada = []
        if dados.get('escopo_edicao') == 'restantes':
            try:
                recorrencia_atualizada = ContaPagarService.reprogramar_recorrencia(
                    conta=conta,
                    quantidade=dados['quantidade_recorrencias'],
                    frequencia=dados['frequencia_recorrencia'],
                    data_vencimento=dados['data_vencimento'],
                    data_competencia=dados.get('data_competencia'),
                    intervalo_dias=dados.get('intervalo_recorrencia_dias'),
                    dias_semana=dados.get('dias_semana_recorrencia'),
                    regra_vencimento_mensal=dados.get('regra_vencimento_mensal'),
                    dia_vencimento_mensal=dados.get('dia_vencimento_mensal'),
                    usuario=request.user,
                )
            except DomainError as exc:
                transaction.set_rollback(True)
                return JsonResponse({'ok': False, 'erro': str(exc)}, status=400)
        conta.refresh_from_db()
        depois = _snapshot_edicao_lancamento(conta, pagamento)
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.AJUSTAR,
            objeto=conta,
            relacionado=pagamento,
            descricao=f'Lancamento da conta a pagar #{conta.pk} editado',
            justificativa=dados['motivo'].strip(),
            antes=antes,
            depois=depois,
            metadados={
                'campos': [campo for campo in CAMPOS_EDICAO_LANCAMENTO if antes.get(campo) != depois.get(campo)],
                'pagamento_ajustado_id': pagamento.pk if pagamento else None,
                'contas_envolvidas': sorted(contas_envolvidas),
                'recorrencia_reprogramada': bool(recorrencia_atualizada),
                'quantidade_recorrencias': len(recorrencia_atualizada),
                'escopo_edicao': dados.get('escopo_edicao') or 'somente',
            },
        )
        if contas_envolvidas:
            # O saldo e materializado para consultas rapidas. Ao mover uma baixa,
            # tanto a conta anterior quanto a nova precisam ser recompostas.
            from apps.financeiro.views.contas_bancarias import ContaBancariaListView

            atualizador = ContaBancariaListView()
            for conta_bancaria in ContaBancaria.objects.for_filial(filial).filter(
                pk__in=contas_envolvidas,
            ):
                atualizador._atualizar_saldo_conta(conta_bancaria)
        return JsonResponse({
            'ok': True,
            'mensagem': (
                f'Lancamento e {len(recorrencia_atualizada)} ocorrencias atualizados.'
                if recorrencia_atualizada
                else 'Lancamento atualizado e registrado no log.'
            ),
            'detail_url': (
                f"{reverse('financeiro:pagar_detail', args=[conta.pk])}?pagamento={pagamento.pk}"
                if pagamento else reverse('financeiro:pagar_detail', args=[conta.pk])
            ),
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

    def _context(self, request, conta, form):
        return {
            'title': f'Pagar — #{conta.pk}',
            'conta': conta,
            'form': form,
            'cancel_url': reverse('financeiro:pagar_detail', args=[conta.pk]),
        }

    def _render_form(self, request, conta, form, status=200):
        template = (
            'financeiro/pagar/_pagamento_modal.html'
            if request.GET.get('modal') == '1'
            else 'financeiro/pagar/pagamento.html'
        )
        return render(request, template, self._context(request, conta, form), status=status)

    def get(self, request, pk):
        conta = self._get_conta(request, pk)
        if conta.status in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]:
            if request.GET.get('modal') == '1':
                return ContaPagarDetailView().get(request, pk)
            messages.warning(request, 'Esta conta não pode ser paga.')
            return redirect(reverse('financeiro:pagar_detail', args=[pk]))

        form = PagamentoContaPagarForm(filial=_filial(request), conta=conta)
        return self._render_form(request, conta, form)

    def post(self, request, pk):
        conta = self._get_conta(request, pk)
        if conta.status in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]:
            if request.GET.get('modal') == '1':
                return ContaPagarDetailView().get(request, pk)
            messages.warning(request, 'Esta conta não pode ser paga.')
            return redirect(reverse('financeiro:pagar_detail', args=[pk]))

        form = PagamentoContaPagarForm(
            request.POST, request.FILES, filial=_filial(request), conta=conta,
        )
        if not form.is_valid():
            return self._render_form(
                request, conta, form,
                status=400 if request.GET.get('modal') == '1' else 200,
            )

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
                tarifa_bancaria=d.get('tarifa_bancaria'),
            )
            if conta.status == StatusContaPagar.PAGO:
                messages.success(request, f'Conta #{pk} paga integralmente. ✓')
            else:
                messages.success(request, f'Pagamento parcial registrado. Saldo restante: R$ {conta.valor_saldo:,.2f}.')
        except DomainError as exc:
            if request.GET.get('modal') == '1':
                form.add_error(None, str(exc))
                return self._render_form(request, conta, form, status=400)
            messages.error(request, str(exc))
            return self._render_form(request, conta, form)

        if request.GET.get('modal') == '1':
            return ContaPagarDetailView().get(request, pk)
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


class ContaPagarExcluirView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    @transaction.atomic
    def post(self, request, pk):
        if not _usuario_admin(request):
            return JsonResponse({'ok': False, 'erro': 'Somente administradores podem excluir títulos.'}, status=403)
        conta = get_object_or_404(ContaPagar.all_objects.for_filial(_filial(request)), pk=pk)
        if conta.excluido:
            return JsonResponse({'ok': False, 'erro': 'Este título já está excluído.'}, status=400)
        motivo = request.POST.get('motivo', '').strip()
        if not motivo:
            return JsonResponse({'ok': False, 'erro': 'Informe o motivo da exclusão.'}, status=400)
        escopo = request.POST.get('escopo_recorrencia', 'somente')
        contas = ContaPagar.all_objects.for_filial(_filial(request)).select_for_update().filter(
            pk=conta.pk,
            excluido_em__isnull=True,
        )
        if escopo == 'restantes' and conta.grupo_recorrencia:
            contas = ContaPagar.all_objects.for_filial(_filial(request)).select_for_update().filter(
                grupo_recorrencia=conta.grupo_recorrencia,
                parcela__gte=conta.parcela,
                excluido_em__isnull=True,
                valor_pago=Decimal('0'),
            ).exclude(
                status=StatusContaPagar.CANCELADO,
            ).order_by('parcela', 'pk')
        contas = list(contas)
        campos = ['excluido_em', 'excluido_por', 'motivo_exclusao']
        excluido_em = timezone.now()
        for titulo in contas:
            antes = snapshot_modelo(titulo, campos)
            titulo.excluido_em = excluido_em
            titulo.excluido_por = request.user
            titulo.motivo_exclusao = motivo
            titulo.save(update_fields=[*campos, 'updated_at'])
            registrar_auditoria(
                request=request,
                modulo=RegistroAuditoria.Modulo.FINANCEIRO,
                acao=RegistroAuditoria.Acao.EXCLUIR,
                objeto=titulo,
                descricao=f'Título a pagar #{titulo.pk} excluído',
                justificativa=motivo,
                antes=antes,
                depois=snapshot_modelo(titulo, campos),
                metadados={
                    'escopo_recorrencia': escopo,
                    'titulo_origem_id': conta.pk,
                    'contas_envolvidas': [titulo.conta_bancaria_id] if titulo.conta_bancaria_id else [],
                },
            )
        return JsonResponse({
            'ok': True,
            'mensagem': f'{len(contas)} título(s) excluído(s) e registrado(s) no log.',
            'quantidade': len(contas),
        })


class ContaPagarRestaurarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    @transaction.atomic
    def post(self, request, pk):
        if not _usuario_admin(request):
            return JsonResponse({'ok': False, 'erro': 'Somente administradores podem restaurar títulos.'}, status=403)
        conta = get_object_or_404(ContaPagar.all_objects.for_filial(_filial(request)), pk=pk)
        if not conta.excluido:
            return JsonResponse({'ok': False, 'erro': 'Este título não está excluído.'}, status=400)
        campos = ['excluido_em', 'excluido_por', 'motivo_exclusao']
        antes = snapshot_modelo(conta, campos)
        motivo_anterior = conta.motivo_exclusao
        conta.excluido_em = None
        conta.excluido_por = None
        conta.motivo_exclusao = ''
        conta.save(update_fields=[*campos, 'updated_at'])
        registrar_auditoria(
            request=request,
            modulo=RegistroAuditoria.Modulo.FINANCEIRO,
            acao=RegistroAuditoria.Acao.RESTAURAR,
            objeto=conta,
            descricao=f'Título a pagar #{conta.pk} restaurado',
            justificativa=request.POST.get('motivo', '').strip() or f'Restauração do título excluído: {motivo_anterior}',
            antes=antes,
            depois=snapshot_modelo(conta, campos),
            metadados={'contas_envolvidas': [conta.conta_bancaria_id] if conta.conta_bancaria_id else []},
        )
        return JsonResponse({'ok': True, 'mensagem': 'Título restaurado.'})
