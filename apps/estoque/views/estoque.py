"""Views de consulta e operacoes de estoque."""
import csv
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Case, Count, DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views import View
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.compras.models import EntradaNF, ItemEntradaNF, PedidoCompra
from apps.compras.services.entrada_custo_service import EntradaCustoService
from apps.core.services.auditoria import auditoria_para_objeto, auditoria_relacionada, registrar_auditoria, snapshot_modelo
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin
from apps.estoque.forms import AjusteEstoqueForm, MovimentacaoManualForm, TransferenciaForm
from apps.estoque.models import Estoque, Inventario, LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.permissoes import (
    bloquear_exportacao_sem_permissao,
    permissoes_estoque,
)
from apps.cadastros.models import Fornecedor
from apps.produtos.models import CategoriaProduto, MarcaProduto, Produto
from apps.produtos.services.prontidao_comercial_service import (
    STATUS_PRONTO,
    anexar_prontidao_produtos,
    avaliar_produtos_para_venda,
)


def _auditar_estoque(request, acao, objeto, descricao='', justificativa='', antes=None, depois=None, relacionado=None, metadados=None):
    return registrar_auditoria(
        request=request,
        modulo='estoque',
        acao=acao,
        objeto=objeto,
        descricao=descricao,
        justificativa=justificativa,
        antes=antes,
        depois=depois,
        relacionado=relacionado,
        metadados=metadados,
    )


def produtos_estoque_queryset(filial):
    estoque_qs = Estoque.objects.filter(
        produto=OuterRef('pk'),
        filial=filial,
    )
    quantidade_field = DecimalField(max_digits=12, decimal_places=3)
    custo_field = DecimalField(max_digits=14, decimal_places=4)
    reposicao_field = DecimalField(max_digits=12, decimal_places=3)
    return Produto.objects.for_filial(filial).filter(
        ativo=True,
    ).select_related(
        'unidade_medida',
        'categoria',
        'subcategoria',
        'fornecedor',
    ).annotate(
        estoque_quantidade_atual=Coalesce(
            Subquery(
                estoque_qs.values('quantidade_atual')[:1],
                output_field=quantidade_field,
            ),
            Value(Decimal('0'), output_field=quantidade_field),
            output_field=quantidade_field,
        ),
        estoque_quantidade_reservada=Coalesce(
            Subquery(
                estoque_qs.values('quantidade_reservada')[:1],
                output_field=quantidade_field,
            ),
            Value(Decimal('0'), output_field=quantidade_field),
            output_field=quantidade_field,
        ),
        estoque_quantidade_disponivel=Coalesce(
            Subquery(
                estoque_qs.values('quantidade_disponivel')[:1],
                output_field=quantidade_field,
            ),
            Value(Decimal('0'), output_field=quantidade_field),
            output_field=quantidade_field,
        ),
        estoque_custo_medio=Coalesce(
            Subquery(
                estoque_qs.values('custo_medio')[:1],
                output_field=custo_field,
            ),
            Value(Decimal('0'), output_field=custo_field),
            output_field=custo_field,
        ),
    ).annotate(
        estoque_custo_unitario=Case(
            When(estoque_custo_medio__gt=0, then=F('estoque_custo_medio')),
            When(preco_custo_medio__gt=0, then=F('preco_custo_medio')),
            default=F('preco_custo'),
            output_field=custo_field,
        ),
        estoque_valor_custo_total=ExpressionWrapper(
            F('estoque_quantidade_atual') * F('estoque_custo_unitario'),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        ),
        sugestao_reposicao=Case(
            When(
                ponto_reposicao__gt=0,
                estoque_quantidade_disponivel__lt=F('ponto_reposicao'),
                estoque_maximo__gt=F('estoque_quantidade_disponivel'),
                then=ExpressionWrapper(
                    F('estoque_maximo') - F('estoque_quantidade_disponivel'),
                    output_field=reposicao_field,
                ),
            ),
            When(
                Q(ponto_reposicao__gt=0)
                & Q(ponto_reposicao__gt=F('estoque_quantidade_disponivel')),
                then=ExpressionWrapper(
                    F('ponto_reposicao') - F('estoque_quantidade_disponivel'),
                    output_field=reposicao_field,
                ),
            ),
            When(
                estoque_minimo__gt=0,
                estoque_quantidade_disponivel__lt=F('estoque_minimo'),
                estoque_maximo__gt=F('estoque_quantidade_disponivel'),
                then=ExpressionWrapper(
                    F('estoque_maximo') - F('estoque_quantidade_disponivel'),
                    output_field=reposicao_field,
                ),
            ),
            When(
                Q(estoque_minimo__gt=0)
                & Q(estoque_minimo__gt=F('estoque_quantidade_disponivel')),
                then=ExpressionWrapper(
                    F('estoque_minimo') - F('estoque_quantidade_disponivel'),
                    output_field=reposicao_field,
                ),
            ),
            default=Value(Decimal('0'), output_field=reposicao_field),
            output_field=reposicao_field,
        ),
    )


class EstoqueListView(PermissaoRequiredMixin, View):
    """Lista consolidada de estoque por produto na filial ativa."""

    permissao_modulo = 'estoque'
    template_name = 'estoque/estoque/list.html'

    def get(self, request):
        base_qs = produtos_estoque_queryset(request.filial_ativa)
        qs = base_qs

        busca = request.GET.get('q', '').strip()
        categoria_id = request.GET.get('categoria', '')
        subcategoria_id = request.GET.get('subcategoria', '')
        marca_id = request.GET.get('marca', '')
        fornecedor_id = request.GET.get('fornecedor', '')
        status = request.GET.get('status') or 'todos'
        ordem = request.GET.get('ordem', 'id')

        if busca:
            filtro_busca = (
                Q(codigo__icontains=busca)
                | Q(descricao__icontains=busca)
                | Q(codigo_barras__icontains=busca)
                | Q(ncm__icontains=busca)
            )
            busca_codigo = busca.lstrip('0')
            if busca_codigo.isdigit():
                codigo_int = int(busca_codigo)
                filtro_busca |= Q(pk=codigo_int) | Q(id_externo=f'produto:{codigo_int}')
            qs = qs.filter(filtro_busca)

        if categoria_id:
            categoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                pk=categoria_id,
                empresa=request.user.empresa,
            ).first()
            subcategoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                pk=subcategoria_id,
                categoria_pai_id=categoria_id,
                empresa=request.user.empresa,
            ).first() if subcategoria_id else None
            if subcategoria:
                filtro_categoria = (
                    Q(subcategoria_id=subcategoria_id)
                    | Q(categoria_id=subcategoria_id)
                    | Q(categoria_id=subcategoria.categoria_pai_id, subcategoria__isnull=True)
                )
                if subcategoria.id_externo:
                    filtro_categoria |= Q(subcategoria__id_externo=subcategoria.id_externo)
                if subcategoria.categoria_pai and subcategoria.categoria_pai.id_externo:
                    filtro_categoria |= Q(
                        categoria__id_externo=subcategoria.categoria_pai.id_externo,
                        subcategoria__isnull=True,
                    )
                qs = qs.filter(filtro_categoria)
            else:
                filtro_categoria = (
                    Q(categoria_id=categoria_id)
                    | Q(categoria__categoria_pai_id=categoria_id)
                )
                if categoria and categoria.id_externo:
                    filtro_categoria |= (
                        Q(categoria__id_externo=categoria.id_externo)
                        | Q(categoria__categoria_pai__id_externo=categoria.id_externo)
                    )
                qs = qs.filter(filtro_categoria)
        elif subcategoria_id and (
            subcategoria := CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                pk=subcategoria_id,
                empresa=request.user.empresa,
                categoria_pai__isnull=False,
            ).first()
        ):
            filtro_categoria = (
                Q(subcategoria_id=subcategoria_id)
                | Q(categoria_id=subcategoria_id)
                | Q(categoria_id=subcategoria.categoria_pai_id, subcategoria__isnull=True)
            )
            if subcategoria.id_externo:
                filtro_categoria |= Q(subcategoria__id_externo=subcategoria.id_externo)
            if subcategoria.categoria_pai and subcategoria.categoria_pai.id_externo:
                filtro_categoria |= Q(
                    categoria__id_externo=subcategoria.categoria_pai.id_externo,
                    subcategoria__isnull=True,
                )
            qs = qs.filter(filtro_categoria)

        if marca_id:
            marca = MarcaProduto.objects.for_filial(request.filial_ativa).filter(
                pk=marca_id,
                empresa=request.user.empresa,
            ).first()
            if marca:
                filtro_marca = Q(marca_id=marca_id)
                if marca.id_externo:
                    filtro_marca |= Q(marca__id_externo=marca.id_externo)
                qs = qs.filter(filtro_marca)

        if fornecedor_id:
            fornecedor = Fornecedor.objects.for_filial(request.filial_ativa).filter(
                pk=fornecedor_id,
            ).first()
            if fornecedor:
                filtro_fornecedor = Q(fornecedor_id=fornecedor_id)
                if fornecedor.id_externo:
                    filtro_fornecedor |= Q(fornecedor__id_externo=fornecedor.id_externo)
                if getattr(fornecedor, 'grupo_replicacao', None):
                    filtro_fornecedor |= Q(fornecedor__grupo_replicacao=fornecedor.grupo_replicacao)
                qs = qs.filter(filtro_fornecedor)

        if status == 'critico':
            qs = qs.filter(
                estoque_minimo__gt=0,
                estoque_quantidade_disponivel__lt=F('estoque_minimo'),
            )
        elif status == 'zerado':
            qs = qs.filter(estoque_quantidade_disponivel__lte=0)
        elif status == 'ok':
            qs = qs.filter(estoque_quantidade_disponivel__gt=0).filter(
                Q(estoque_minimo__lte=0)
                | Q(estoque_quantidade_disponivel__gte=F('estoque_minimo'))
            )
        elif status == 'pendente_custo':
            qs = qs.filter(estoque_custo_unitario__lte=0)
        elif status == 'pendente_fiscal_cadastro':
            qs = qs.filter(Q(categoria__isnull=True) | Q(codigo_barras=''))
        elif status == 'pendente_comercial':
            qs = qs.filter(Q(preco_venda__lte=0) | Q(rascunho_comercial=True))

        export = request.GET.get('export')
        if export in {'csv', 'pdf'}:
            bloqueio = bloquear_exportacao_sem_permissao(request)
            if bloqueio:
                return bloqueio
            qs_export = qs.order_by('descricao')
            if export == 'pdf':
                return self._exportar_pdf(qs_export)
            return self._exportar_csv(qs_export)

        valor_custo_expr = ExpressionWrapper(
            F('estoque_quantidade_atual') * F('estoque_custo_unitario'),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        )
        valor_venda_expr = ExpressionWrapper(
            F('estoque_quantidade_atual') * F('preco_venda'),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        )
        resumo = base_qs.aggregate(
            total_itens=Count('id'),
            valor_custo_total=Sum(valor_custo_expr),
            valor_venda_total=Sum(valor_venda_expr),
        )
        resumo.update({
            'abaixo_minimo': base_qs.filter(
                estoque_minimo__gt=0,
                estoque_quantidade_disponivel__lt=F('estoque_minimo'),
            ).count(),
            'zerados': base_qs.filter(estoque_quantidade_disponivel__lte=0).count(),
        })

        ordenacoes = {
            'id': 'id',
            'id_desc': '-id',
            'referencia': 'codigo',
            'referencia_desc': '-codigo',
            'az': 'descricao',
            'za': '-descricao',
            'atual': 'estoque_quantidade_atual',
            'atual_desc': '-estoque_quantidade_atual',
            'disponivel': 'estoque_quantidade_disponivel',
            'disponivel_desc': '-estoque_quantidade_disponivel',
            'custo': 'estoque_custo_unitario',
            'custo_desc': '-estoque_custo_unitario',
            'custo_total': 'estoque_valor_custo_total',
            'custo_total_desc': '-estoque_valor_custo_total',
            'preco': 'preco_venda',
            'preco_desc': '-preco_venda',
        }
        qs = qs.order_by(ordenacoes.get(ordem, 'id'))
        page_obj = Paginator(qs, 30).get_page(request.GET.get('page'))
        anexar_prontidao_produtos(page_obj.object_list, filial=request.filial_ativa)
        querydict = request.GET.copy()
        querydict.pop('page', None)
        querydict.pop('export', None)
        sort_urls = {}
        for key, value in {
            'id': 'id_desc' if ordem == 'id' else 'id',
            'referencia': 'referencia_desc' if ordem == 'referencia' else 'referencia',
            'nome': 'za' if ordem == 'az' else 'az',
            'atual': 'atual_desc' if ordem == 'atual' else 'atual',
            'disponivel': 'disponivel_desc' if ordem == 'disponivel' else 'disponivel',
            'custo': 'custo_desc' if ordem == 'custo' else 'custo',
            'custo_total': 'custo_total_desc' if ordem == 'custo_total' else 'custo_total',
            'preco': 'preco_desc' if ordem == 'preco' else 'preco',
        }.items():
            params = request.GET.copy()
            params.pop('page', None)
            params['ordem'] = value
            sort_urls[key] = params.urlencode()

        return render(request, self.template_name, {
            'page_obj': page_obj,
            'produtos_estoque': page_obj.object_list,
            'busca': busca,
            'categoria_id': categoria_id,
            'subcategoria_id': subcategoria_id,
            'marca_id': marca_id,
            'fornecedor_id': fornecedor_id,
            'status': status,
            'ordem': ordem,
            'sort_urls': sort_urls,
            'categorias': CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
                categoria_pai__isnull=True,
            ).order_by('nome'),
            'subcategorias': CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
                categoria_pai_id=categoria_id,
            ).order_by('nome') if categoria_id else CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
                categoria_pai__isnull=False,
            ).order_by('categoria_pai__nome', 'nome'),
            'subcategorias_por_categoria_json': json.dumps({
                '': [
                    {'id': item.id, 'nome': item.nome}
                    for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                        empresa=request.user.empresa,
                        ativo=True,
                        categoria_pai__isnull=False,
                    ).order_by('categoria_pai__nome', 'nome')
                ],
                **{
                    str(categoria_id): [
                        {'id': item.id, 'nome': item.nome}
                        for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                            empresa=request.user.empresa,
                            ativo=True,
                            categoria_pai_id=categoria_id,
                        ).order_by('nome')
                    ]
                    for categoria_id in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                        empresa=request.user.empresa,
                        ativo=True,
                        categoria_pai__isnull=True,
                    ).values_list('id', flat=True)
                },
            }),
            'marcas': MarcaProduto.objects.for_filial(request.filial_ativa).filter(
                empresa=request.user.empresa,
                ativo=True,
            ).order_by('nome'),
            'fornecedores': Fornecedor.objects.for_filial(request.filial_ativa).filter(
                ativo=True,
            ).order_by('nome_fantasia', 'razao_social'),
            'resumo': resumo,
            'permissoes_estoque': permissoes_estoque(request),
            'pode_editar_produto': request.user.tem_permissao('produtos', 'editar'),
            'pode_editar_estoque': request.user.tem_permissao('estoque', 'editar'),
            'inline_categorias_json': json.dumps([
                {'id': item.id, 'nome': item.nome}
                for item in CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                    empresa=request.user.empresa,
                    ativo=True,
                    categoria_pai__isnull=True,
                ).order_by('nome')
            ]),
            'inline_fornecedores_json': json.dumps([
                {'id': item.id, 'nome': str(item)}
                for item in Fornecedor.objects.for_filial(request.filial_ativa).filter(
                    ativo=True,
                ).order_by('nome_fantasia', 'razao_social')
            ]),
            'page_querystring': querydict.urlencode(),
        })

    @staticmethod
    def _exportar_csv(qs):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="estoque.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Produto ID',
            'Codigo',
            'Codigo barras',
            'Descricao',
            'Unidade',
            'Atual',
            'Reservado',
            'Disponivel',
            'Minimo',
            'Reposicao sugerida',
            'Preco venda',
            'Custo unitario',
            'Custo total',
        ])
        for produto in qs:
            writer.writerow([
                produto.codigo_replicacao,
                produto.codigo,
                produto.codigo_barras,
                produto.descricao,
                produto.unidade_medida.sigla if produto.unidade_medida_id else '',
                produto.estoque_quantidade_atual,
                produto.estoque_quantidade_reservada,
                produto.estoque_quantidade_disponivel,
                produto.estoque_minimo,
                produto.sugestao_reposicao,
                produto.preco_atual,
                produto.estoque_custo_unitario,
                produto.estoque_valor_custo_total,
            ])
        return response

    @staticmethod
    def _formatar_moeda(valor):
        if valor is None:
            valor = Decimal('0')
        valor = Decimal(str(valor))
        return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    @staticmethod
    def _formatar_quantidade(valor):
        if valor is None:
            valor = Decimal('0')
        valor = Decimal(str(valor))
        casas = 0 if valor == valor.to_integral_value() else 3
        texto = f'{valor:,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        return texto.rstrip('0').rstrip(',') if casas else texto

    @classmethod
    def _exportar_pdf(cls, qs):
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="estoque.pdf"'
        doc = SimpleDocTemplate(
            response,
            pagesize=landscape(A4),
            rightMargin=20,
            leftMargin=20,
            topMargin=24,
            bottomMargin=24,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph('Relatorio de estoque', styles['Title']),
            Spacer(1, 10),
        ]
        data = [[
            'Produto',
            'Atual',
            'Disponivel',
            'Minimo',
            'Preco venda',
            'Custo unit.',
            'Custo total',
            'Status',
        ]]
        for produto in qs:
            if produto.estoque_quantidade_disponivel <= 0:
                status = 'Zerado'
            elif produto.estoque_minimo > 0 and produto.estoque_quantidade_disponivel < produto.estoque_minimo:
                status = 'Critico'
            else:
                status = 'OK'
            data.append([
                produto.descricao,
                cls._formatar_quantidade(produto.estoque_quantidade_atual),
                cls._formatar_quantidade(produto.estoque_quantidade_disponivel),
                cls._formatar_quantidade(produto.estoque_minimo),
                cls._formatar_moeda(produto.preco_atual),
                cls._formatar_moeda(produto.estoque_custo_unitario),
                cls._formatar_moeda(produto.estoque_valor_custo_total),
                status,
            ])
        table = Table(data, repeatRows=1, colWidths=[190, 55, 65, 55, 75, 75, 80, 55])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('ALIGN', (1, 1), (6, -1), 'RIGHT'),
            ('ALIGN', (7, 1), (7, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return response


class EstoqueKardexProdutoView(PermissaoRequiredMixin, View):
    """Resumo operacional do produto na filial ativa para a sobreposicao da lista."""

    permissao_modulo = 'estoque'
    TIPOS_CONSUMO = (
        MovimentacaoEstoque.TipoOperacao.SAIDA,
        MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
        MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS,
        MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_FORNECEDOR,
        MovimentacaoEstoque.TipoOperacao.BAIXA_VALIDADE,
        MovimentacaoEstoque.TipoOperacao.USO_PROPRIO,
        MovimentacaoEstoque.TipoOperacao.BRINDE,
        MovimentacaoEstoque.TipoOperacao.QUEBRA,
        MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
    )

    @staticmethod
    def _formatar_moeda_opcional(valor):
        if valor in (None, ''):
            return '-'
        try:
            return EstoqueListView._formatar_moeda(valor)
        except Exception:
            return '-'

    @classmethod
    def _dados_custo_movimento(cls, mov):
        anterior = mov.custo_medio_anterior or Decimal('0')
        posterior = mov.custo_medio_posterior or Decimal('0')
        unitario = mov.valor_unitario or Decimal('0')
        custo_label = ''

        if anterior != posterior:
            custo_label = (
                f'Custo medio: {cls._formatar_moeda_opcional(mov.custo_medio_anterior)}'
                f' -> {cls._formatar_moeda_opcional(mov.custo_medio_posterior)}'
            )
        elif posterior > 0:
            custo_label = f'Custo medio mantido: {cls._formatar_moeda_opcional(mov.custo_medio_posterior)}'

        return {
            'tem_custo_relevante': bool(custo_label or unitario > 0),
            'custo_medio_label': custo_label,
            'custo_medio_display': cls._formatar_moeda_opcional(mov.custo_medio_posterior) if posterior > 0 else '-',
            'custo_medio_anterior_display': cls._formatar_moeda_opcional(mov.custo_medio_anterior) if anterior > 0 else '-',
            'custo_medio_variou': anterior != posterior,
            'custo_unitario_movimento_label': cls._formatar_moeda_opcional(mov.valor_unitario) if unitario > 0 else '',
        }

    @classmethod
    def _calcular_giro(cls, produto, filial, quantidade_disponivel):
        desde = timezone.now() - timedelta(days=30)
        consumo_total = (
            MovimentacaoEstoque.objects
            .for_filial(filial)
            .filter(
                produto=produto,
                tipo_operacao__in=cls.TIPOS_CONSUMO,
                data_movimentacao__gte=desde,
            )
            .aggregate(total=Coalesce(
                Sum('quantidade'),
                Decimal('0'),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            ))['total']
            or Decimal('0')
        )
        consumo_medio_dia = consumo_total / Decimal('30')
        if consumo_medio_dia > 0:
            cobertura = quantidade_disponivel / consumo_medio_dia
            cobertura_dias = int(cobertura.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            cobertura_label = f'{cobertura_dias} dia' if cobertura_dias == 1 else f'{cobertura_dias} dias'
            status = 'ok' if cobertura >= Decimal('7') else 'critico'
        else:
            cobertura = None
            cobertura_dias = None
            cobertura_label = 'Sem consumo recente'
            status = 'sem_consumo'
        return {
            'periodo_dias': 30,
            'consumo_total': EstoqueListView._formatar_quantidade(consumo_total),
            'consumo_medio_dia': EstoqueListView._formatar_quantidade(consumo_medio_dia),
            'giro_label': f'{EstoqueListView._formatar_quantidade(consumo_medio_dia)}/dia',
            'giro_mensal_label': f'{EstoqueListView._formatar_quantidade(consumo_total)}/mês',
            'cobertura_dias': str(cobertura_dias) if cobertura_dias is not None else '',
            'cobertura_label': cobertura_label,
            'status': status,
        }

    @classmethod
    def _historico_preco_custo(cls, produto, movimentacoes):
        historico = []
        campos_preco = {
            'preco_venda': 'Preco venda',
            'preco_custo': 'Preco custo',
            'preco_custo_medio': 'Custo medio',
            'preco_promocional': 'Preco promocional',
        }
        registros = list(auditoria_para_objeto(produto, limit=20))
        vistos = set()
        for registro in registros:
            chave = registro.pk
            if chave in vistos:
                continue
            vistos.add(chave)
            antes = registro.dados_anteriores or {}
            depois = registro.dados_novos or {}
            mudancas = []
            for campo, label in campos_preco.items():
                if campo in antes or campo in depois:
                    antigo = antes.get(campo)
                    novo = depois.get(campo)
                    if str(antigo) != str(novo):
                        mudancas.append(
                            f'{label}: {cls._formatar_moeda_opcional(antigo)} -> {cls._formatar_moeda_opcional(novo)}'
                        )
            if mudancas:
                historico.append({
                    'data': timezone.localtime(registro.criado_em).strftime('%d/%m/%Y %H:%M') if registro.criado_em else '-',
                    'tipo': 'Cadastro',
                    'descricao': ' | '.join(mudancas[:3]),
                    'origem': registro.get_acao_display(),
                })
            if len(historico) >= 5:
                break

        for mov in movimentacoes:
            if len(historico) >= 8:
                break
            if not (mov.custo_medio_anterior is not None or mov.custo_medio_posterior is not None or mov.valor_unitario is not None):
                continue
            if (
                (mov.custo_medio_anterior or Decimal('0')) == (mov.custo_medio_posterior or Decimal('0'))
                and not (mov.valor_unitario and mov.valor_unitario > 0)
            ):
                continue
            dados_custo = cls._dados_custo_movimento(mov)
            historico.append({
                'data': mov.data_movimentacao.strftime('%d/%m/%Y %H:%M') if mov.data_movimentacao else '-',
                'tipo': mov.get_tipo_operacao_display(),
                'descricao': dados_custo['custo_medio_label'],
                'origem': f'Unitario movimento {cls._formatar_moeda_opcional(mov.valor_unitario)}',
            })

        if not historico:
            historico.append({
                'data': timezone.localtime().strftime('%d/%m/%Y %H:%M'),
                'tipo': 'Atual',
                'descricao': (
                    f'Preco venda: {EstoqueListView._formatar_moeda(produto.preco_atual)} | '
                    f'Custo atual: {EstoqueListView._formatar_moeda(produto.estoque_custo_unitario)}'
                ),
                'origem': 'Cadastro atual',
            })
        return historico[:8]

    def get(self, request, pk):
        produto = get_object_or_404(
            produtos_estoque_queryset(request.filial_ativa),
            pk=pk,
        )
        avaliacao = avaliar_produtos_para_venda([produto], filial=request.filial_ativa).get(produto.pk)
        estoque = Estoque.objects.filter(produto=produto, filial=request.filial_ativa).first()
        quantidade_atual = estoque.quantidade_atual if estoque else Decimal('0')
        quantidade_reservada = estoque.quantidade_reservada if estoque else Decimal('0')
        quantidade_disponivel = estoque.quantidade_disponivel if estoque else Decimal('0')
        reposicao = EstoqueInlineEditView._sugestao_reposicao(produto, quantidade_disponivel)
        custo_unitario = produto.estoque_custo_unitario or Decimal('0')
        valor_custo_total = quantidade_atual * custo_unitario
        valor_venda_total = quantidade_atual * (produto.preco_atual or Decimal('0'))
        estoque_minimo = produto.estoque_minimo or Decimal('0')
        abaixo_minimo = estoque_minimo > 0 and quantidade_disponivel < estoque_minimo
        falta_minimo = (estoque_minimo - quantidade_disponivel) if abaixo_minimo else Decimal('0')
        giro = self._calcular_giro(produto, request.filial_ativa, quantidade_disponivel)

        movimentacoes = (
            MovimentacaoEstoque.objects
            .for_filial(request.filial_ativa)
            .filter(produto=produto)
            .select_related('lote', 'usuario', 'filial_destino')
            .order_by('-data_movimentacao')[:8]
        )
        lotes = (
            LoteProduto.objects
            .filter(produto=produto, filial=request.filial_ativa)
            .order_by('data_validade', '-quantidade_atual')[:8]
        )

        def fmt_qtd(valor):
            return EstoqueListView._formatar_quantidade(valor)

        movimentacoes_payload = []
        for mov in movimentacoes:
            dados_custo = self._dados_custo_movimento(mov)
            movimentacoes_payload.append({
                'data': mov.data_movimentacao.strftime('%d/%m/%Y %H:%M') if mov.data_movimentacao else '-',
                'tipo': mov.get_tipo_operacao_display(),
                'quantidade': fmt_qtd(mov.quantidade),
                'anterior': fmt_qtd(mov.quantidade_anterior),
                'posterior': fmt_qtd(mov.quantidade_posterior),
                'saldo_apos': fmt_qtd(mov.quantidade_posterior),
                'documento': mov.documento_numero or mov.get_documento_tipo_display() or '-',
                'lote': mov.lote.numero_lote if mov.lote_id else '-',
                'valor': EstoqueListView._formatar_moeda(mov.valor_unitario or 0),
                'custo_medio_anterior': self._formatar_moeda_opcional(mov.custo_medio_anterior),
                'custo_medio_posterior': self._formatar_moeda_opcional(mov.custo_medio_posterior),
                'custo_unitario_movimento': self._formatar_moeda_opcional(mov.valor_unitario),
                **dados_custo,
            })

        return JsonResponse({
            'produto': {
                'id': produto.codigo_replicacao,
                'pk': produto.pk,
                'descricao': produto.descricao,
                'codigo': produto.codigo or '-',
                'codigo_barras': produto.codigo_barras or '-',
                'foto_url': produto.foto_url or '',
                'unidade': produto.unidade_medida.sigla if produto.unidade_medida_id else '-',
                'categoria': produto.categoria.nome if produto.categoria_id else '-',
                'fornecedor': str(produto.fornecedor) if produto.fornecedor_id else '-',
                'controla_lote': produto.controla_lote,
                'controla_validade': produto.controla_validade,
            },
            'estoque': {
                'atual': fmt_qtd(quantidade_atual),
                'reservado': fmt_qtd(quantidade_reservada),
                'disponivel': fmt_qtd(quantidade_disponivel),
                'minimo': fmt_qtd(produto.estoque_minimo),
                'reposicao': fmt_qtd(reposicao) if reposicao > 0 else '-',
                'preco_venda': EstoqueListView._formatar_moeda(produto.preco_atual),
                'custo_unitario': EstoqueListView._formatar_moeda(custo_unitario),
                'valor_custo_total': EstoqueListView._formatar_moeda(valor_custo_total),
                'valor_venda_total': EstoqueListView._formatar_moeda(valor_venda_total),
                'abaixo_minimo': abaixo_minimo,
                'falta_minimo': fmt_qtd(falta_minimo),
                'alerta_minimo': (
                    f'Saldo disponivel abaixo do minimo. Faltam {fmt_qtd(falta_minimo)} {produto.unidade_medida.sigla if produto.unidade_medida_id else ""}.'
                    if abaixo_minimo else ''
                ),
            },
            'giro': giro,
            'prontidao': {
                'label': avaliacao['label'] if avaliacao else 'Nao avaliado',
                'status': avaliacao['status'] if avaliacao else '',
                'pendencias': [item['label'] for item in (avaliacao or {}).get('pendencias', [])],
            },
            'movimentacoes': movimentacoes_payload,
            'historico_precos': self._historico_preco_custo(produto, movimentacoes),
            'lotes': [
                {
                    'numero': lote.numero_lote,
                    'quantidade': fmt_qtd(lote.quantidade_atual),
                    'validade': lote.data_validade.strftime('%d/%m/%Y') if lote.data_validade else '-',
                    'status': lote.get_status_display(),
                    'custo': EstoqueListView._formatar_moeda(lote.custo_unitario),
                }
                for lote in lotes
            ],
            'links': {
                'produto': reverse('produtos:produto-update', args=[produto.pk]),
                'movimentar': f"{reverse('estoque:movimentacao-create')}?produto={produto.pk}",
                'movimentacoes': f"{reverse('estoque:movimentacao-list')}?produto={produto.pk}",
                'lotes': f"{reverse('estoque:lote-list')}?{urlencode({'q': produto.codigo or produto.descricao})}",
            },
        })


class EstoqueInlineEditView(PermissaoRequiredMixin, View):
    """Edicao rapida dos campos principais do produto na listagem de estoque."""

    permissao_modulo = 'estoque'
    permissao_acao = 'editar'

    CAMPOS_PRODUTO = {'descricao', 'categoria', 'fornecedor', 'estoque_minimo'}
    CAMPOS_ESTOQUE = {'estoque_atual'}

    def post(self, request, pk):
        produto = get_object_or_404(
            produtos_estoque_queryset(request.filial_ativa), pk=pk,
        )
        field = request.POST.get('field', '').strip()
        value = request.POST.get('value', '').strip()
        if field not in self.CAMPOS_PRODUTO | self.CAMPOS_ESTOQUE:
            return JsonResponse({'ok': False, 'error': 'Campo nao permitido.'}, status=400)
        if field in self.CAMPOS_ESTOQUE:
            return self._atualizar_estoque(request, produto, value)
        return self._atualizar_produto(request, produto, field, value)

    def _atualizar_produto(self, request, produto, field, value):
        from apps.produtos.views.produto import (
            _decimal_from_request,
            _format_quantidade_produto,
            _produto_audit_changes,
            _produto_audit_snapshot,
            _registrar_produto_log,
            _sincronizar_produto_sem_quebrar,
        )

        snapshot_antes = _produto_audit_snapshot(produto)
        try:
            with transaction.atomic():
                if field == 'descricao':
                    value = value.strip()
                    if not value:
                        return JsonResponse({
                            'ok': False,
                            'error': 'Nome do produto e obrigatorio.',
                        }, status=400)
                    produto.descricao = value[:150]
                elif field == 'categoria':
                    categoria = CategoriaProduto.objects.for_filial(request.filial_ativa).filter(
                        empresa=request.user.empresa,
                        ativo=True,
                        categoria_pai__isnull=True,
                        pk=value,
                    ).first() if value else None
                    if value and not categoria:
                        return JsonResponse({
                            'ok': False,
                            'error': 'Categoria invalida.',
                        }, status=400)
                    produto.categoria = categoria
                    if produto.subcategoria and (
                        not categoria or produto.subcategoria.categoria_pai_id != categoria.pk
                    ):
                        produto.subcategoria = None
                elif field == 'fornecedor':
                    fornecedor = Fornecedor.objects.for_filial(request.filial_ativa).filter(
                        ativo=True,
                        pk=value,
                    ).first() if value else None
                    if value and not fornecedor:
                        return JsonResponse({
                            'ok': False,
                            'error': 'Fornecedor invalido.',
                        }, status=400)
                    produto.fornecedor = fornecedor
                elif field == 'estoque_minimo':
                    quantidade = _decimal_from_request(value)
                    if quantidade < 0:
                        return JsonResponse({
                            'ok': False,
                            'error': 'Estoque minimo nao pode ser negativo.',
                        }, status=400)
                    produto.estoque_minimo = quantidade

                produto.calcular_margem()
                produto.save()
                changes = _produto_audit_changes(snapshot_antes, _produto_audit_snapshot(produto))
                if changes:
                    _registrar_produto_log(
                        request,
                        produto,
                        'Produto editado',
                        f'Edicao rapida na lista de estoque: {", ".join(change["campo"] for change in changes)}.',
                        changes=changes,
                    )
        except (InvalidOperation, ValueError):
            return JsonResponse({'ok': False, 'error': 'Valor invalido.'}, status=400)

        _sincronizar_produto_sem_quebrar(request, produto)
        displays = self._displays_estoque(produto, request.filial_ativa, _format_quantidade_produto)
        return JsonResponse({
            'ok': True,
            'display': self._display(produto, field, _format_quantidade_produto),
            'value': self._value(produto, field),
            **displays,
        })

    def _atualizar_estoque(self, request, produto, value):
        from apps.produtos.views.produto import (
            _format_quantidade_produto,
            _registrar_produto_log,
            _decimal_from_request,
        )

        estoque = Estoque.objects.filter(produto=produto, filial=request.filial_ativa).first()
        quantidade_atual = estoque.quantidade_atual if estoque else Decimal('0')
        try:
            quantidade_nova = _decimal_from_request(value)
        except Exception:
            return JsonResponse({'ok': False, 'error': 'Quantidade de estoque invalida.'}, status=400)
        if quantidade_nova == quantidade_atual:
            displays = self._displays_estoque(produto, request.filial_ativa, _format_quantidade_produto)
            return JsonResponse({
                'ok': True,
                'display': _format_quantidade_produto(quantidade_atual, produto),
                'value': str(quantidade_atual),
                **displays,
            })

        try:
            MovimentacaoService.ajustar_manual(
                produto_id=produto.pk,
                filial_id=request.filial_ativa.pk,
                quantidade_nova=quantidade_nova,
                usuario_id=request.user.pk,
                justificativa='Edicao rapida de estoque na lista de estoque.',
            )
        except Exception as exc:
            return JsonResponse({
                'ok': False,
                'error': str(exc) or 'Nao foi possivel ajustar o estoque.',
            }, status=400)

        _registrar_produto_log(
            request,
            produto,
            'Ajuste de estoque',
            'Edicao rapida de estoque na lista de estoque.',
            changes=[{
                'campo': 'Estoque',
                'antes': _format_quantidade_produto(quantidade_atual, produto),
                'depois': _format_quantidade_produto(quantidade_nova, produto),
            }],
        )
        displays = self._displays_estoque(produto, request.filial_ativa, _format_quantidade_produto)
        return JsonResponse({
            'ok': True,
            'display': _format_quantidade_produto(quantidade_nova, produto),
            'value': str(quantidade_nova),
            **displays,
        })

    def _display(self, produto, field, format_quantidade):
        if field == 'categoria':
            return produto.categoria.nome if produto.categoria else '-'
        if field == 'fornecedor':
            return str(produto.fornecedor) if produto.fornecedor_id else '-'
        if field == 'estoque_minimo':
            return format_quantidade(produto.estoque_minimo, produto)
        return getattr(produto, field) or '-'

    def _value(self, produto, field):
        if field == 'categoria':
            return produto.categoria_id or ''
        if field == 'fornecedor':
            return produto.fornecedor_id or ''
        return getattr(produto, field) or ''

    def _displays_estoque(self, produto, filial, format_quantidade):
        estoque = Estoque.objects.filter(produto=produto, filial=filial).first()
        quantidade_atual = estoque.quantidade_atual if estoque else Decimal('0')
        quantidade_disponivel = estoque.quantidade_disponivel if estoque else Decimal('0')
        custo_unitario = (
            (estoque.custo_medio if estoque and estoque.custo_medio > 0 else None)
            or produto.preco_custo_medio
            or produto.preco_custo
            or Decimal('0')
        )
        reposicao = self._sugestao_reposicao(produto, quantidade_disponivel)
        valor_custo_total = quantidade_atual * custo_unitario
        return {
            'estoque_atual_display': format_quantidade(quantidade_atual, produto),
            'estoque_atual_value': str(quantidade_atual),
            'estoque_minimo_display': format_quantidade(produto.estoque_minimo, produto),
            'estoque_minimo_value': str(produto.estoque_minimo),
            'reposicao_display': format_quantidade(reposicao, produto) if reposicao > 0 else '-',
            'custo_total_display': EstoqueListView._formatar_moeda(valor_custo_total),
        }

    @staticmethod
    def _sugestao_reposicao(produto, quantidade_disponivel):
        estoque_maximo = produto.estoque_maximo or Decimal('0')
        ponto_reposicao = produto.ponto_reposicao or Decimal('0')
        estoque_minimo = produto.estoque_minimo or Decimal('0')
        if (
            ponto_reposicao > 0
            and quantidade_disponivel < ponto_reposicao
            and estoque_maximo > quantidade_disponivel
        ):
            return estoque_maximo - quantidade_disponivel
        if ponto_reposicao > 0 and ponto_reposicao > quantidade_disponivel:
            return ponto_reposicao - quantidade_disponivel
        if (
            estoque_minimo > 0
            and quantidade_disponivel < estoque_minimo
            and estoque_maximo > quantidade_disponivel
        ):
            return estoque_maximo - quantidade_disponivel
        if estoque_minimo > 0 and estoque_minimo > quantidade_disponivel:
            return estoque_minimo - quantidade_disponivel
        return Decimal('0')


class EntradaCustoEstoqueListView(PermissaoRequiredMixin, View):
    """Painel de estoque para acompanhar custo composto das entradas."""

    permissao_modulo = 'estoque'
    template_name = 'estoque/estoque/custos_entrada.html'

    def get(self, request):
        qs_base = (
            EntradaNF.objects.for_filial(request.filial_ativa)
            .select_related('fornecedor', 'usuario', 'usuario_efetivacao')
            .annotate(total_itens=Count('itens'))
        )
        qs = qs_base

        busca = request.GET.get('q', '').strip()
        status = request.GET.get('status', '')
        custo = request.GET.get('custo', 'todos')

        if busca:
            qs = qs.filter(
                Q(numero_nf__icontains=busca)
                | Q(chave_acesso_nf__icontains=busca)
                | Q(fornecedor__razao_social__icontains=busca)
                | Q(fornecedor__nome_fantasia__icontains=busca)
                | Q(emitente_razao_social_xml__icontains=busca)
                | Q(emitente_cnpj_xml__icontains=busca)
            )
        if status:
            qs = qs.filter(status=status)

        abertas = [
            EntradaNF.Status.RASCUNHO,
            EntradaNF.Status.AGUARDANDO_VINCULOS,
            EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            EntradaNF.Status.COM_DIFERENCAS,
            EntradaNF.Status.CONFERIDA,
        ]
        tem_componentes = (
            Q(valor_frete__gt=0)
            | Q(valor_seguro__gt=0)
            | Q(valor_outras_despesas__gt=0)
            | Q(valor_desconto__gt=0)
            | Q(valor_ipi__gt=0)
            | Q(valor_icms_st__gt=0)
            | Q(custo_financeiro__gt=0)
            | Q(custo_incluir_icms=True, valor_icms__gt=0)
        )
        if custo == 'pendente':
            qs = qs.filter(status__in=abertas, custo_composto_em__isnull=True).filter(tem_componentes)
        elif custo == 'aplicado':
            qs = qs.filter(custo_composto_em__isnull=False)
        elif custo == 'componentes':
            qs = qs.filter(tem_componentes)
        elif custo == 'sem_componentes':
            qs = qs.exclude(tem_componentes)

        qs = qs.order_by('-data_entrada', '-pk')
        page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
        entradas = list(page_obj.object_list)
        for entrada in entradas:
            entrada.tem_componentes_custo = any([
                entrada.valor_frete,
                entrada.valor_seguro,
                entrada.valor_outras_despesas,
                entrada.valor_desconto,
                entrada.valor_ipi,
                entrada.valor_icms_st,
                entrada.custo_financeiro,
                entrada.custo_incluir_icms and entrada.valor_icms,
            ])
            entrada.custo_pendente_revisao = (
                entrada.status in abertas
                and entrada.tem_componentes_custo
                and not entrada.custo_composto_em
            )
            entrada.alertas_custo_entrada = []
            entrada.alertas_custo_criticos = 0
            try:
                composicao = EntradaCustoService.compor(
                    entrada=entrada,
                    metodo_rateio=entrada.custo_rateio_metodo,
                    incluir_ipi=entrada.custo_incluir_ipi,
                    incluir_icms_st=entrada.custo_incluir_icms_st,
                    incluir_icms=entrada.custo_incluir_icms,
                    custo_financeiro=entrada.custo_financeiro or Decimal('0'),
                )
                entrada.alertas_custo_entrada = composicao.get('alertas_custo', [])
                entrada.alertas_custo_criticos = composicao['resumo'].get(
                    'alertas_custo_criticos',
                    0,
                )
            except DomainError:
                entrada.alertas_custo_entrada = []
                entrada.alertas_custo_criticos = 0

        kpis = {
            'entradas': qs_base.count(),
            'com_componentes': qs_base.filter(tem_componentes).count(),
            'pendentes': qs_base.filter(
                status__in=abertas,
                custo_composto_em__isnull=True,
            ).filter(tem_componentes).count(),
            'aplicadas': qs_base.filter(custo_composto_em__isnull=False).count(),
            'alertas': sum(len(entrada.alertas_custo_entrada) for entrada in entradas),
        }
        componentes = qs.aggregate(
            frete=Coalesce(Sum('valor_frete'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            seguro=Coalesce(Sum('valor_seguro'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            outras=Coalesce(Sum('valor_outras_despesas'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            desconto=Coalesce(Sum('valor_desconto'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            ipi=Coalesce(Sum('valor_ipi'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            icms_st=Coalesce(Sum('valor_icms_st'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            icms=Coalesce(Sum('valor_icms'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            financeiro=Coalesce(Sum('custo_financeiro'), Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2)),
        )

        return render(request, self.template_name, {
            'entradas': entradas,
            'page_obj': page_obj,
            'busca': busca,
            'status': status,
            'custo': custo,
            'status_choices': EntradaNF.Status.choices,
            'kpis': kpis,
            'componentes': componentes,
            'pode_editar_custos_entrada': request.user.tem_permissao('compras', 'editar'),
        })


class RelatorioEstoqueView(PermissaoRequiredMixin, View):
    """Relatorios operacionais do estoque da filial e comparativo por filial."""

    permissao_modulo = 'estoque'
    template_name = 'estoque/relatorios/list.html'

    def get(self, request):
        filial = request.filial_ativa
        produtos = produtos_estoque_queryset(filial)
        valor_custo_expr = ExpressionWrapper(
            F('estoque_quantidade_atual') * F('estoque_custo_unitario'),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        )
        valor_venda_expr = ExpressionWrapper(
            F('estoque_quantidade_atual') * F('preco_venda'),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        )
        resumo = produtos.aggregate(
            skus=Count('id'),
            valor_custo_total=Sum(valor_custo_expr),
            valor_venda_total=Sum(valor_venda_expr),
            quantidade_total=Sum('estoque_quantidade_atual'),
        )
        resumo.update({
            'criticos': produtos.filter(
                estoque_minimo__gt=0,
                estoque_quantidade_disponivel__lt=F('estoque_minimo'),
            ).count(),
            'zerados': produtos.filter(estoque_quantidade_disponivel__lte=0).count(),
        })

        hoje = timezone.localdate()
        lotes = LoteProduto.objects.for_filial(filial).select_related('produto', 'fornecedor')
        lotes_vencidos = lotes.filter(
            quantidade_atual__gt=0,
            data_validade__lt=hoje,
        ).order_by('data_validade')[:10]
        lotes_proximos = lotes.filter(
            quantidade_atual__gt=0,
            data_validade__gte=hoje,
            data_validade__lte=hoje + timedelta(days=30),
        ).order_by('data_validade')[:10]

        divergencias = (
            Inventario.objects.for_filial(filial)
            .filter(status=Inventario.Status.FECHADO)
            .select_related('usuario_fechamento')
            .annotate(
                divergencias_total=Count(
                    'itens',
                    filter=Q(itens__diferenca__isnull=False) & ~Q(itens__diferenca=0),
                    distinct=True,
                ),
            )
            .filter(divergencias_total__gt=0)
            .order_by('-data_fim', '-pk')[:8]
        )

        ultimo_custo = (
            ItemEntradaNF.objects
            .filter(
                produto_id=OuterRef('pk'),
                entrada__filial=filial,
                entrada__status=EntradaNF.Status.EFETIVADA,
                custo_unitario_total__gt=0,
            )
            .order_by('-entrada__data_entrada', '-pk')
        )
        custo_field = DecimalField(max_digits=14, decimal_places=4)
        produtos_custo = list(
            produtos.annotate(
                ultimo_custo_entrada=Coalesce(
                    Subquery(
                        ultimo_custo.values('custo_unitario_total')[:1],
                        output_field=custo_field,
                    ),
                    Value(Decimal('0'), output_field=custo_field),
                    output_field=custo_field,
                )
            ).filter(ultimo_custo_entrada__gt=0)[:200]
        )
        custos_divergentes = []
        for produto in produtos_custo:
            referencia = produto.estoque_custo_unitario or Decimal('0')
            if referencia <= 0:
                continue
            variacao = (
                ((produto.ultimo_custo_entrada - referencia) / referencia) * Decimal('100')
            ).quantize(Decimal('0.01'))
            if abs(variacao) >= Decimal('20'):
                produto.variacao_custo_entrada = variacao
                custos_divergentes.append(produto)
        custos_divergentes.sort(
            key=lambda item: abs(item.variacao_custo_entrada),
            reverse=True,
        )

        valor_custo_estoque_expr = ExpressionWrapper(
            F('quantidade_atual') * F('custo_medio'),
            output_field=DecimalField(max_digits=18, decimal_places=4),
        )
        saldos_filiais = (
            Estoque.objects
            .filter(filial__empresa=request.user.empresa)
            .values('filial_id', 'filial__nome_fantasia', 'filial__razao_social', 'filial__uf')
            .annotate(
                skus=Count('produto_id', distinct=True),
                quantidade_total=Sum('quantidade_atual'),
                valor_custo_total=Sum(valor_custo_estoque_expr),
            )
            .order_by('filial__nome_fantasia', 'filial__razao_social')
        )

        permissoes = permissoes_estoque(request)
        matriz_permissoes = [
            ('Movimentacao manual', 'estoque:criar', permissoes['pode_movimentar']),
            ('Ajuste manual', 'estoque:editar', permissoes['pode_ajustar']),
            ('Transferencia entre filiais', 'estoque:aprovar', permissoes['pode_transferir']),
            ('Inventario: abrir', 'estoque:criar', permissoes['pode_abrir_inventario']),
            ('Inventario: contar', 'estoque:editar', permissoes['pode_contar_inventario']),
            ('Inventario: fechar', 'estoque:aprovar', permissoes['pode_fechar_inventario']),
            ('Baixa por validade', 'estoque:cancelar', permissoes['pode_baixar_validade']),
            ('Exportar dados', 'estoque:exportar', permissoes['pode_exportar']),
            ('Custos de entrada', 'compras:editar', request.user.tem_permissao('compras', 'editar')),
        ]

        return render(request, self.template_name, {
            'resumo': resumo,
            'lotes_vencidos': lotes_vencidos,
            'lotes_proximos': lotes_proximos,
            'divergencias': divergencias,
            'custos_divergentes': custos_divergentes[:10],
            'saldos_filiais': saldos_filiais,
            'matriz_permissoes': matriz_permissoes,
        })


class ReposicaoListView(PermissaoRequiredMixin, View):
    """Plano operacional de reposicao a partir dos pontos de estoque."""

    permissao_modulo = 'estoque'
    template_name = 'estoque/reposicao/list.html'

    def get_queryset(self, filial):
        return produtos_estoque_queryset(filial).filter(
            sugestao_reposicao__gt=0,
        ).order_by('descricao')

    def get(self, request):
        qs = self.get_queryset(request.filial_ativa)
        export = request.GET.get('export')
        if export in {'csv', 'pdf'}:
            bloqueio = bloquear_exportacao_sem_permissao(request, 'estoque:reposicao-list')
            if bloqueio:
                return bloqueio
            if export == 'pdf':
                return self._exportar_pdf(qs)
            return self._exportar_csv(qs)

        produtos = list(qs)
        self._enriquecer_reposicao(produtos, filial=request.filial_ativa)
        resumo = {
            'total': len(produtos),
            'com_fornecedor': sum(1 for produto in produtos if produto.fornecedor_id),
            'sem_fornecedor': sum(1 for produto in produtos if not produto.fornecedor_id),
            'com_pedido_aberto': sum(1 for produto in produtos if produto.reposicao_status == 'pedido_gerado'),
            'aguardando_entrada': sum(1 for produto in produtos if produto.reposicao_status == 'aguardando_entrada'),
            'com_pendencia_prontidao': sum(1 for produto in produtos if produto.prontidao_comercial['status'] != STATUS_PRONTO),
            'quantidade_total': sum((produto.sugestao_reposicao for produto in produtos), Decimal('0')),
            'valor_estimado': sum(
                (produto.valor_reposicao_estimado for produto in produtos),
                Decimal('0'),
            ),
        }
        return render(request, self.template_name, {
            'produtos': produtos,
            'resumo': resumo,
            'permissoes_estoque': permissoes_estoque(request),
            'pode_gerar_pedido': (
                request.user.tem_permissao('estoque', 'editar')
                and request.user.tem_permissao('compras', 'criar')
            ),
        })

    def post(self, request):
        if not request.user.tem_permissao('estoque', 'editar'):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('estoque:reposicao-list')
        if not request.user.tem_permissao('compras', 'criar'):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('estoque:reposicao-list')

        ids = request.POST.getlist('produto')
        if not ids:
            messages.error(request, 'Selecione ao menos um produto para gerar reposicao.')
            return redirect('estoque:reposicao-list')

        produtos = list(
            self.get_queryset(request.filial_ativa)
            .filter(pk__in=ids)
            .select_related('fornecedor')
        )
        self._enriquecer_reposicao(produtos, filial=request.filial_ativa)
        try:
            self._aplicar_quantidades_post(request, produtos)
        except DomainError as e:
            messages.error(request, str(e))
            return redirect('estoque:reposicao-list')

        pedidos = self._gerar_pedidos_compra(request, produtos)
        sem_fornecedor = [produto for produto in produtos if not produto.fornecedor_id]
        com_pendencia = [
            produto for produto in produtos
            if getattr(produto, 'prontidao_comercial', {}).get('status') != STATUS_PRONTO
        ]

        if pedidos:
            numeros = ', '.join(pedido.numero_pedido for pedido in pedidos)
            messages.success(request, f'Pedidos de compra em rascunho gerados: {numeros}.')
        if sem_fornecedor:
            messages.warning(
                request,
                f'{len(sem_fornecedor)} produto(s) ficaram sem pedido por falta de fornecedor.',
            )
        if com_pendencia:
            messages.warning(
                request,
                f'{len(com_pendencia)} produto(s) foram enviados com alerta de prontidao comercial. Revise antes de aprovar o pedido.',
            )
        if not pedidos:
            messages.error(request, 'Nenhum pedido foi gerado. Verifique fornecedores dos produtos.')
            return redirect('estoque:reposicao-list')
        if len(pedidos) == 1:
            return redirect('compras:pedido-detail', pk=pedidos[0].pk)
        return redirect('compras:pedido-list')

    @classmethod
    def _aplicar_quantidades_post(cls, request, produtos):
        for produto in produtos:
            quantidade = cls._quantidade_reposicao_from_request(request, produto)
            produto.quantidade_reposicao_acao = quantidade

    @classmethod
    def _quantidade_reposicao_from_request(cls, request, produto):
        default = produto.sugestao_reposicao
        mobile = cls._parse_quantidade_reposicao(
            request.POST.get(f'quantidade_mobile_{produto.pk}'),
            default,
            produto,
        )
        desktop = cls._parse_quantidade_reposicao(
            request.POST.get(f'quantidade_desktop_{produto.pk}'),
            default,
            produto,
        )
        if desktop != default:
            return desktop
        if mobile != default:
            return mobile
        return desktop

    @staticmethod
    def _parse_quantidade_reposicao(value, default, produto):
        if value is None or str(value).strip() == '':
            return default
        text = str(value).strip()
        if ',' in text and '.' in text:
            text = text.replace('.', '').replace(',', '.')
        elif ',' in text:
            text = text.replace(',', '.')
        try:
            quantidade = Decimal(text).quantize(Decimal('0.001'))
        except (InvalidOperation, ValueError):
            raise DomainError(f'Quantidade invalida para {produto.descricao}.')
        if quantidade <= 0:
            raise DomainError(f'Quantidade de reposicao deve ser positiva para {produto.descricao}.')
        return quantidade

    @staticmethod
    def _enriquecer_reposicao(produtos, filial=None):
        avaliacoes = avaliar_produtos_para_venda(produtos, filial=filial)
        pedidos_por_produto = ReposicaoListView._pedidos_abertos_por_produto(filial, produtos)
        for produto in produtos:
            custo = (
                produto.estoque_custo_unitario
                or produto.preco_custo_medio
                or produto.preco_custo
                or Decimal('0')
            )
            produto.custo_reposicao_base = custo
            produto.valor_reposicao_estimado = (produto.sugestao_reposicao or Decimal('0')) * custo
            produto.criterio_reposicao = ReposicaoListView._criterio_reposicao(produto)
            produto.prontidao_comercial = avaliacoes.get(produto.pk)
            produto.pedido_reposicao_aberto = pedidos_por_produto.get(produto.pk)
            produto.reposicao_status = ReposicaoListView._status_fluxo_reposicao(produto)
            produto.reposicao_status_label = {
                'gerar_pedido': 'Gerar pedido',
                'pedido_gerado': 'Pedido ja gerado',
                'aguardando_entrada': 'Aguardando entrada',
                'sem_fornecedor': 'Sem fornecedor',
            }.get(produto.reposicao_status, 'Gerar pedido')
            if produto.lead_time_reposicao_dias:
                sufixo = 'dia' if produto.lead_time_reposicao_dias == 1 else 'dias'
                produto.lead_time_label = f'{produto.lead_time_reposicao_dias} {sufixo}'
            else:
                produto.lead_time_label = 'Nao informado'

    @staticmethod
    def _pedidos_abertos_por_produto(filial, produtos):
        if not filial or not produtos:
            return {}
        produto_ids = [produto.pk for produto in produtos]
        itens = (
            PedidoCompra.objects
            .for_filial(filial)
            .filter(
                status__in=[
                    PedidoCompra.Status.RASCUNHO,
                    PedidoCompra.Status.AGUARDANDO_APROVACAO,
                    PedidoCompra.Status.APROVADO,
                    PedidoCompra.Status.ENVIADO_FORNECEDOR,
                    PedidoCompra.Status.CONFIRMADO_FORNECEDOR,
                    PedidoCompra.Status.PARCIALMENTE_RECEBIDO,
                ],
                itens__produto_id__in=produto_ids,
            )
            .select_related('fornecedor')
            .prefetch_related('itens')
            .distinct()
            .order_by('-data_emissao')
        )
        por_produto = {}
        for pedido in itens:
            for item in pedido.itens.all():
                if item.produto_id in produto_ids and item.produto_id not in por_produto:
                    por_produto[item.produto_id] = pedido
        return por_produto

    @staticmethod
    def _status_fluxo_reposicao(produto):
        pedido = getattr(produto, 'pedido_reposicao_aberto', None)
        if not produto.fornecedor_id:
            return 'sem_fornecedor'
        if not pedido:
            return 'gerar_pedido'
        if pedido.status in (
            PedidoCompra.Status.APROVADO,
            PedidoCompra.Status.ENVIADO_FORNECEDOR,
            PedidoCompra.Status.CONFIRMADO_FORNECEDOR,
            PedidoCompra.Status.PARCIALMENTE_RECEBIDO,
        ):
            return 'aguardando_entrada'
        return 'pedido_gerado'

    @staticmethod
    def _criterio_reposicao(produto):
        disponivel = produto.estoque_quantidade_disponivel or Decimal('0')
        if produto.ponto_reposicao > 0 and disponivel < produto.ponto_reposicao:
            if produto.estoque_maximo > disponivel:
                return 'Abaixo do ponto; recompor ate o maximo'
            return 'Abaixo do ponto de reposicao'
        if produto.estoque_minimo > 0 and disponivel < produto.estoque_minimo:
            if produto.estoque_maximo > disponivel:
                return 'Abaixo do minimo; recompor ate o maximo'
            return 'Abaixo do minimo'
        return 'Reposicao sugerida'

    @staticmethod
    def _formatar_moeda(valor):
        if valor is None:
            valor = Decimal('0')
        valor = Decimal(str(valor))
        return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    @staticmethod
    def _gerar_pedidos_compra(request, produtos):
        from collections import defaultdict

        from apps.compras.models import PedidoCompra
        from apps.compras.services.compra_service import CompraService

        observacao_reposicao = 'Gerado pelo plano de reposicao de estoque. Origem: reposicao_estoque.'
        grupos = defaultdict(list)
        for produto in produtos:
            if produto.fornecedor_id:
                grupos[produto.fornecedor].append(produto)

        pedidos = []
        for fornecedor, produtos_fornecedor in grupos.items():
            pedido = (
                PedidoCompra.objects
                .filter(
                    filial=request.filial_ativa,
                    fornecedor=fornecedor,
                    status=PedidoCompra.Status.RASCUNHO,
                    observacao__icontains='plano de reposicao de estoque',
                )
                .order_by('-data_emissao')
                .first()
            )
            if not pedido:
                pedido = CompraService.criar_pedido(
                    filial=request.filial_ativa,
                    usuario=request.user,
                    fornecedor=fornecedor,
                    observacao=observacao_reposicao,
                )
                criado_agora = True
            else:
                criado_agora = False
            for produto in produtos_fornecedor:
                valor_unitario = (
                    produto.estoque_custo_unitario
                    or getattr(produto, 'custo_reposicao_base', Decimal('0'))
                    or produto.preco_custo_medio
                    or produto.preco_custo
                    or produto.estoque_custo_medio
                    or Decimal('0')
                )
                quantidade = getattr(produto, 'quantidade_reposicao_acao', produto.sugestao_reposicao)
                item = pedido.itens.filter(produto=produto).first()
                if item:
                    item.quantidade = quantidade
                    item.valor_unitario = valor_unitario
                    item.valor_ipi = Decimal('0')
                    item.calcular_totais()
                    item.save(update_fields=[
                        'quantidade',
                        'valor_unitario',
                        'valor_bruto',
                        'valor_ipi',
                        'valor_total',
                        'updated_at',
                    ])
                    pedido.recalcular_totais()
                    pedido.save(update_fields=[
                        'valor_produtos',
                        'valor_desconto',
                        'valor_ipi',
                        'frete_valor',
                        'valor_total',
                        'updated_at',
                    ])
                else:
                    item = CompraService.adicionar_item(
                        pedido=pedido,
                        produto=produto,
                        quantidade=quantidade,
                        valor_unitario=valor_unitario,
                    )
                item.observacao = (
                    f'Reposicao estoque: disponivel {produto.estoque_quantidade_disponivel}; '
                    f'minimo {produto.estoque_minimo}; ponto {produto.ponto_reposicao}; '
                    f'sugestao original {produto.sugestao_reposicao}; criterio {produto.criterio_reposicao}.'
                )[:255]
                item.save(update_fields=['observacao', 'updated_at'])
                registrar_auditoria(
                    request=request,
                    modulo='estoque',
                    acao='criar',
                    objeto=pedido,
                    descricao=f'Pedido {pedido.numero_pedido} gerado pelo plano de reposicao',
                    justificativa='Reposicao automatizada a partir de estoque abaixo do minimo/ponto.',
                    relacionado=produto,
                    depois=snapshot_modelo(pedido),
                    metadados={
                        'produto_id': produto.pk,
                        'quantidade': str(quantidade),
                        'valor_unitario': str(valor_unitario),
                        'pedido_criado_agora': criado_agora,
                        'prontidao_status': getattr(produto, 'prontidao_comercial', {}).get('status'),
                    },
                )
            pedidos.append(pedido)
        return pedidos

    @staticmethod
    def _exportar_csv(qs):
        produtos = list(qs)
        ReposicaoListView._enriquecer_reposicao(produtos)
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="reposicao_estoque.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Produto ID',
            'Codigo',
            'Produto',
            'Fornecedor',
            'Atual',
            'Disponivel',
            'Minimo',
            'Ponto reposicao',
            'Maximo',
            'Lead time',
            'Criterio',
            'Reposicao sugerida',
            'Custo base',
            'Valor estimado',
        ])
        for produto in produtos:
            writer.writerow([
                produto.codigo_replicacao,
                produto.codigo,
                produto.descricao,
                produto.fornecedor.razao_social if produto.fornecedor_id else '',
                produto.estoque_quantidade_atual,
                produto.estoque_quantidade_disponivel,
                produto.estoque_minimo,
                produto.ponto_reposicao,
                produto.estoque_maximo,
                produto.lead_time_label,
                produto.criterio_reposicao,
                produto.sugestao_reposicao,
                produto.custo_reposicao_base,
                produto.valor_reposicao_estimado,
            ])
        return response

    @staticmethod
    def _exportar_pdf(qs):
        produtos = list(qs)
        ReposicaoListView._enriquecer_reposicao(produtos)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="reposicao_estoque.pdf"'
        doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=18, leftMargin=18)
        styles = getSampleStyleSheet()
        elements = [
            Paragraph('Plano de reposicao de estoque', styles['Title']),
            Paragraph('Itens abaixo do minimo ou ponto de reposicao da filial atual.', styles['BodyText']),
            Spacer(1, 10),
        ]
        data = [[
            'Produto',
            'Fornecedor',
            'Disponivel',
            'Min.',
            'Ponto',
            'Max.',
            'Sugestao',
            'Custo',
            'Valor',
        ]]
        for produto in produtos:
            data.append([
                produto.descricao[:42],
                (produto.fornecedor.razao_social if produto.fornecedor_id else '-')[:32],
                str(produto.estoque_quantidade_disponivel),
                str(produto.estoque_minimo),
                str(produto.ponto_reposicao),
                str(produto.estoque_maximo),
                str(produto.sugestao_reposicao),
                ReposicaoListView._formatar_moeda(produto.custo_reposicao_base),
                ReposicaoListView._formatar_moeda(produto.valor_reposicao_estimado),
            ])
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return response

class MovimentacaoManualView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'criar'
    template_name = 'estoque/movimentacao/operacao.html'

    def get(self, request):
        initial = {}
        produto_id = request.GET.get('produto')
        if produto_id:
            initial['produto'] = produto_id
        return render(request, self.template_name, {
            'form': MovimentacaoManualForm(initial=initial, filial=request.filial_ativa),
            'title': 'Nova movimentacao',
            'cancel_url': reverse_lazy('estoque:estoque-list'),
        })

    def post(self, request):
        form = MovimentacaoManualForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                mov = MovimentacaoService.registrar_movimentacao(
                    produto_id=form.cleaned_data['produto'].pk,
                    filial_id=request.filial_ativa.pk,
                    tipo_operacao=form.cleaned_data['tipo_operacao'],
                    quantidade=form.cleaned_data['quantidade'],
                    usuario_id=request.user.pk,
                    lote_id=form.cleaned_data['lote'].pk if form.cleaned_data.get('lote') else None,
                    valor_unitario=form.cleaned_data.get('valor_unitario'),
                    documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                    documento_numero=form.cleaned_data.get('documento_numero', ''),
                    observacao=form.cleaned_data.get('observacao', ''),
                )
                _auditar_estoque(
                    request,
                    'criar',
                    mov,
                    f'Movimentacao manual #{mov.pk}',
                    justificativa=form.cleaned_data.get('observacao', ''),
                    depois=snapshot_modelo(mov),
                    relacionado=form.cleaned_data['produto'],
                    metadados={
                        'tipo_operacao': mov.tipo_operacao,
                        'quantidade': str(mov.quantidade),
                        'saldo_anterior': str(mov.quantidade_anterior),
                        'saldo_posterior': str(mov.quantidade_posterior),
                    },
                )
                messages.success(request, 'Movimentacao registrada.')
                return redirect('estoque:movimentacao-list')
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova movimentacao',
            'cancel_url': reverse_lazy('estoque:estoque-list'),
        })


class AjusteEstoqueView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'editar'
    template_name = 'estoque/movimentacao/ajuste.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': AjusteEstoqueForm(filial=request.filial_ativa),
            'title': 'Ajuste manual de estoque',
            'cancel_url': reverse_lazy('estoque:estoque-list'),
        })

    def post(self, request):
        form = AjusteEstoqueForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                mov = MovimentacaoService.ajustar_manual(
                    produto_id=form.cleaned_data['produto'].pk,
                    filial_id=request.filial_ativa.pk,
                    quantidade_nova=form.cleaned_data['quantidade_nova'],
                    usuario_id=request.user.pk,
                    justificativa=form.cleaned_data['justificativa'],
                    lote_id=form.cleaned_data['lote'].pk if form.cleaned_data.get('lote') else None,
                )
                _auditar_estoque(
                    request,
                    'ajustar',
                    mov,
                    f'Ajuste manual #{mov.pk}',
                    justificativa=form.cleaned_data['justificativa'],
                    depois=snapshot_modelo(mov),
                    relacionado=form.cleaned_data['produto'],
                    metadados={
                        'quantidade_nova': str(form.cleaned_data['quantidade_nova']),
                        'saldo_anterior': str(mov.quantidade_anterior),
                        'saldo_posterior': str(mov.quantidade_posterior),
                    },
                )
                messages.success(request, 'Ajuste aplicado.')
                return redirect('estoque:estoque-list')
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'title': 'Ajuste manual de estoque',
            'cancel_url': reverse_lazy('estoque:estoque-list'),
        })


class AjusteRapidoEstoqueView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'editar'
    template_name = 'estoque/estoque/ajuste_rapido.html'
    session_key = 'ajuste_rapido_estoque'

    @staticmethod
    def _decimal_from_text(value):
        text = str(value or '').strip()
        text = text.replace('.', '').replace(',', '.') if ',' in text else text
        if not text:
            return Decimal('0')
        try:
            quantidade = Decimal(text)
        except (InvalidOperation, ValueError):
            raise DomainError('Quantidade invalida.')
        if quantidade < 0:
            raise DomainError('Quantidade nao pode ser negativa.')
        return quantidade.quantize(Decimal('0.001'))

    @staticmethod
    def _decimal_to_value(value):
        value = Decimal(str(value or '0'))
        return f'{value:.3f}'.rstrip('0').rstrip('.')

    @staticmethod
    def _session_items(request):
        dados = request.session.get(AjusteRapidoEstoqueView.session_key, {})
        return dados if isinstance(dados, dict) else {}

    @classmethod
    def _store_session_item(cls, request, produto, atual_anterior, contado, delta, saldo_final, movimento=None, conferido=True):
        itens = cls._session_items(request)
        agora = timezone.localtime().strftime('%d/%m/%Y %H:%M')
        itens[str(produto.pk)] = {
            'produto_pk': produto.pk,
            'produto_id': produto.codigo_replicacao,
            'codigo': produto.codigo or '',
            'codigo_barras': produto.codigo_barras or '',
            'descricao': produto.descricao,
            'atual_anterior': cls._decimal_to_value(atual_anterior),
            'contado': cls._decimal_to_value(contado),
            'diferenca': cls._decimal_to_value(delta),
            'saldo_final': cls._decimal_to_value(saldo_final),
            'movimento_id': movimento.pk if movimento else '',
            'conferido': bool(conferido),
            'usuario': getattr(request.user, 'nome', '') or getattr(request.user, 'username', '') or str(request.user),
            'data': agora,
        }
        request.session[cls.session_key] = itens
        request.session.modified = True
        return itens[str(produto.pk)]

    @staticmethod
    def _buscar_produtos(request):
        qs = produtos_estoque_queryset(request.filial_ativa)
        busca = request.GET.get('q', '').strip()
        barcode = request.GET.get('barcode', '').strip()
        status_conferencia = request.GET.get('status_conferencia', 'todos')
        if status_conferencia not in {'todos', 'conferidos', 'pendentes'}:
            status_conferencia = 'todos'

        termo = barcode or busca
        if termo:
            filtro = (
                Q(descricao__icontains=termo)
                | Q(descricao_curta__icontains=termo)
                | Q(descricao_pdv__icontains=termo)
                | Q(codigo__icontains=termo)
                | Q(codigo_barras__icontains=termo)
                | Q(ncm__icontains=termo)
                | Q(categoria__nome__icontains=termo)
                | Q(subcategoria__nome__icontains=termo)
                | Q(fornecedor__nome_fantasia__icontains=termo)
                | Q(fornecedor__razao_social__icontains=termo)
            )
            codigo_limpo = termo.lstrip('0')
            if codigo_limpo.isdigit():
                codigo_int = int(codigo_limpo)
                filtro |= Q(pk=codigo_int) | Q(id_externo=f'produto:{codigo_int}')
            qs = qs.filter(filtro)

        if status_conferencia in {'conferidos', 'pendentes'}:
            ids_conferidos = [
                int(pk)
                for pk, item in AjusteRapidoEstoqueView._session_items(request).items()
                if item.get('conferido')
            ]
            if status_conferencia == 'conferidos':
                qs = qs.filter(pk__in=ids_conferidos) if ids_conferidos else qs.none()
            elif ids_conferidos:
                qs = qs.exclude(pk__in=ids_conferidos)

        return qs.order_by('descricao', 'pk'), busca, barcode, status_conferencia

    @staticmethod
    def _foto_url_segura(request, produto):
        url = (produto.foto_url or '').strip()
        if not url:
            return ''
        if request.is_secure() and url.startswith('http://'):
            return 'https://' + url[7:]
        if url.startswith('/'):
            return request.build_absolute_uri(url)
        return url

    def get(self, request):
        qs, busca, barcode, status_conferencia = self._buscar_produtos(request)
        page_obj = Paginator(qs, 80).get_page(request.GET.get('page'))
        sessao = self._session_items(request)
        for produto in page_obj.object_list:
            item = sessao.get(str(produto.pk), {})
            produto.ajuste_rapido_conferido = bool(item.get('conferido'))
            produto.ajuste_rapido_editado = str(produto.pk) in sessao
            produto.ajuste_rapido_contado = item.get('contado', self._decimal_to_value(produto.estoque_quantidade_atual))
            produto.estoque_quantidade_atual_value = self._decimal_to_value(produto.estoque_quantidade_atual)
            produto.estoque_quantidade_atual_display = EstoqueListView._formatar_quantidade(produto.estoque_quantidade_atual)
            produto.ajuste_rapido_foto_url = self._foto_url_segura(request, produto)

        querydict = request.GET.copy()
        querydict.pop('page', None)
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'produtos': page_obj.object_list,
            'busca': busca,
            'barcode': barcode,
            'status_conferencia': status_conferencia,
            'itens_sessao': sessao,
            'itens_sessao_total': len(sessao),
            'itens_conferidos_total': sum(1 for item in sessao.values() if item.get('conferido')),
            'page_querystring': querydict.urlencode(),
        })


class AjusteRapidoEstoqueAtualizarView(AjusteRapidoEstoqueView):
    def post(self, request):
        produto = get_object_or_404(
            produtos_estoque_queryset(request.filial_ativa),
            pk=request.POST.get('produto_id'),
        )
        try:
            quantidade_contada = self._decimal_from_text(request.POST.get('quantidade'))
        except DomainError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        estoque = Estoque.objects.filter(produto=produto, filial=request.filial_ativa).first()
        quantidade_atual = estoque.quantidade_atual if estoque else Decimal('0')
        delta = quantidade_contada - quantidade_atual
        movimento = None
        if delta:
            tipo = (
                MovimentacaoEstoque.TipoOperacao.AJUSTE_MAIS
                if delta > 0 else MovimentacaoEstoque.TipoOperacao.AJUSTE_MENOS
            )
            try:
                movimento = MovimentacaoService.registrar_movimentacao(
                    produto_id=produto.pk,
                    filial_id=request.filial_ativa.pk,
                    tipo_operacao=tipo,
                    quantidade=abs(delta),
                    usuario_id=request.user.pk,
                    documento_tipo=MovimentacaoEstoque.DocumentoTipo.AJUSTE_MANUAL,
                    documento_numero='AJUSTE-RAPIDO',
                    observacao=(
                        'Ajuste rapido de conferencia. '
                        f'Contado: {self._decimal_to_value(quantidade_contada)}; '
                        f'anterior: {self._decimal_to_value(quantidade_atual)}.'
                    ),
                    permitir_sem_lote=True,
                )
            except DomainError as exc:
                return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
            _auditar_estoque(
                request,
                'ajustar',
                movimento,
                f'Ajuste rapido de estoque #{movimento.pk}',
                justificativa='Conferencia rapida de estoque',
                depois=snapshot_modelo(movimento),
                relacionado=produto,
                metadados={
                    'quantidade_contada': str(quantidade_contada),
                    'saldo_anterior': str(quantidade_atual),
                    'saldo_posterior': str(movimento.quantidade_posterior),
                    'origem': 'ajuste_rapido',
                },
            )
            saldo_final = movimento.quantidade_posterior
        else:
            saldo_final = quantidade_atual

        item = self._store_session_item(
            request,
            produto,
            quantidade_atual,
            quantidade_contada,
            delta,
            saldo_final,
            movimento=movimento,
            conferido=request.POST.get('conferido', '1') != '0',
        )
        return JsonResponse({
            'ok': True,
            'produto_id': produto.pk,
            'quantidade_atual': self._decimal_to_value(saldo_final),
            'quantidade_atual_display': EstoqueListView._formatar_quantidade(saldo_final),
            'diferenca': self._decimal_to_value(delta),
            'diferenca_display': EstoqueListView._formatar_quantidade(delta),
            'movimento_id': movimento.pk if movimento else None,
            'item': item,
            'totais': {
                'editados': len(self._session_items(request)),
                'conferidos': sum(1 for item in self._session_items(request).values() if item.get('conferido')),
            },
        })


class AjusteRapidoEstoqueLogView(AjusteRapidoEstoqueView):
    def get(self, request, pk):
        produto = get_object_or_404(produtos_estoque_queryset(request.filial_ativa), pk=pk)
        movimentacoes = (
            MovimentacaoEstoque.objects
            .for_filial(request.filial_ativa)
            .filter(produto=produto)
            .select_related('usuario', 'lote')
            .order_by('-data_movimentacao')[:18]
        )
        return JsonResponse({
            'produto': {
                'id': produto.codigo_replicacao,
                'descricao': produto.descricao,
            },
            'movimentacoes': [
                {
                    'data': timezone.localtime(mov.data_movimentacao).strftime('%d/%m/%Y %H:%M') if mov.data_movimentacao else '-',
                    'tipo': mov.get_tipo_operacao_display(),
                    'quantidade': EstoqueListView._formatar_quantidade(mov.quantidade),
                    'anterior': EstoqueListView._formatar_quantidade(mov.quantidade_anterior),
                    'posterior': EstoqueListView._formatar_quantidade(mov.quantidade_posterior),
                    'usuario': getattr(mov.usuario, 'nome', '') or str(mov.usuario) if mov.usuario_id else 'Sistema',
                    'documento': mov.documento_numero or mov.get_documento_tipo_display() or '-',
                    'observacao': mov.observacao or '',
                }
                for mov in movimentacoes
            ],
        })


class AjusteRapidoEstoquePdfView(AjusteRapidoEstoqueView):
    def get(self, request):
        itens = list(self._session_items(request).values())
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="ajuste_rapido_estoque.pdf"'
        doc = SimpleDocTemplate(
            response,
            pagesize=landscape(A4),
            rightMargin=22,
            leftMargin=22,
            topMargin=24,
            bottomMargin=24,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph('Ajuste rapido de estoque', styles['Title']),
            Paragraph(
                f"Filial: {request.filial_ativa} | Usuario: {request.user} | Gerado em {timezone.localtime().strftime('%d/%m/%Y %H:%M')}",
                styles['Normal'],
            ),
            Spacer(1, 10),
        ]
        data = [[
            'ID', 'Codigo', 'Codigo barras', 'Produto', 'Anterior', 'Contado',
            'Diferenca', 'Final', 'Conferido', 'Mov.'
        ]]
        for item in itens:
            data.append([
                item.get('produto_id', ''),
                item.get('codigo', ''),
                item.get('codigo_barras', ''),
                item.get('descricao', ''),
                item.get('atual_anterior', ''),
                item.get('contado', ''),
                item.get('diferenca', ''),
                item.get('saldo_final', ''),
                'Sim' if item.get('conferido') else 'Nao',
                item.get('movimento_id', '') or '-',
            ])
        if len(data) == 1:
            data.append(['-', '-', '-', 'Nenhum ajuste registrado nesta sessao.', '-', '-', '-', '-', '-', '-'])
        table = Table(data, repeatRows=1, colWidths=[38, 58, 82, 235, 58, 58, 58, 58, 55, 42])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#111827')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('ALIGN', (4, 1), (9, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return response


class AjusteRapidoEstoqueLimparView(AjusteRapidoEstoqueView):
    def post(self, request):
        request.session.pop(self.session_key, None)
        request.session.modified = True
        messages.success(request, 'Conferencia limpa.')
        return redirect('estoque:ajuste-rapido')


class TransferenciaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'aprovar'
    template_name = 'estoque/movimentacao/transferencia.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': TransferenciaForm(
                filial=request.filial_ativa,
                empresa=request.user.empresa,
            ),
            'title': 'Transferencia entre filiais',
            'cancel_url': reverse_lazy('estoque:estoque-list'),
        })

    def post(self, request):
        form = TransferenciaForm(
            request.POST,
            filial=request.filial_ativa,
            empresa=request.user.empresa,
        )
        if form.is_valid():
            try:
                mov_saida, mov_entrada = MovimentacaoService.transferir_entre_filiais(
                    produto_id=form.cleaned_data['produto'].pk,
                    filial_origem_id=request.filial_ativa.pk,
                    filial_destino_id=form.cleaned_data['filial_destino'].pk,
                    quantidade=form.cleaned_data['quantidade'],
                    usuario_id=request.user.pk,
                    lote_id=form.cleaned_data['lote'].pk if form.cleaned_data.get('lote') else None,
                    observacao=form.cleaned_data.get('observacao', ''),
                )
                _auditar_estoque(
                    request,
                    'transferir',
                    mov_saida,
                    f'Transferencia de estoque #{mov_saida.pk}/{mov_entrada.pk}',
                    justificativa=form.cleaned_data.get('observacao', ''),
                    depois=snapshot_modelo(mov_saida),
                    relacionado=mov_entrada,
                    metadados={
                        'produto_id': form.cleaned_data['produto'].pk,
                        'filial_origem_id': request.filial_ativa.pk,
                        'filial_destino_id': form.cleaned_data['filial_destino'].pk,
                        'quantidade': str(form.cleaned_data['quantidade']),
                        'movimento_entrada_id': mov_entrada.pk,
                    },
                )
                messages.success(
                    request,
                    f'Transferencia efetuada. Saida #{mov_saida.pk} / Entrada #{mov_entrada.pk}.',
                )
                return redirect('estoque:movimentacao-list')
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'title': 'Transferencia entre filiais',
            'cancel_url': reverse_lazy('estoque:estoque-list'),
        })


class MovimentacaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/movimentacao/list.html'

    def get(self, request):
        qs = MovimentacaoEstoque.objects.for_filial(request.filial_ativa).select_related(
            'filial',
            'filial_destino',
            'produto',
            'usuario',
            'lote',
        )

        busca = request.GET.get('q', '').strip()
        tipo = request.GET.get('tipo', '')
        data_inicio = request.GET.get('data_inicio', '').strip()
        data_fim = request.GET.get('data_fim', '').strip()
        produto_id = request.GET.get('produto', '').strip()
        documento_tipo = request.GET.get('documento_tipo', '').strip()
        documento_id = request.GET.get('documento_id', '').strip()
        produto_filtrado = None

        if busca:
            filtro_busca = (
                Q(produto__codigo__icontains=busca)
                | Q(produto__descricao__icontains=busca)
                | Q(produto__codigo_barras__icontains=busca)
                | Q(lote__numero_lote__icontains=busca)
                | Q(documento_numero__icontains=busca)
                | Q(observacao__icontains=busca)
                | Q(filial_destino__nome_fantasia__icontains=busca)
                | Q(filial_destino__razao_social__icontains=busca)
            )
            busca_codigo = busca.lstrip('0')
            if busca_codigo.isdigit():
                codigo_int = int(busca_codigo)
                filtro_busca |= (
                    Q(pk=codigo_int)
                    | Q(documento_id=codigo_int)
                    | Q(produto__pk=codigo_int)
                    | Q(produto__id_externo=f'produto:{codigo_int}')
                )
            qs = qs.filter(filtro_busca)
        if tipo:
            qs = qs.filter(tipo_operacao=tipo)
        data_inicio_parseada = parse_date(data_inicio) if data_inicio else None
        data_fim_parseada = parse_date(data_fim) if data_fim else None
        if data_inicio_parseada:
            qs = qs.filter(data_movimentacao__date__gte=data_inicio_parseada)
        if data_fim_parseada:
            qs = qs.filter(data_movimentacao__date__lte=data_fim_parseada)
        if produto_id:
            produto_filtrado = Produto.objects.for_filial(request.filial_ativa).filter(
                pk=produto_id,
            ).first()
            if produto_filtrado:
                qs = qs.filter(produto=produto_filtrado)
            else:
                qs = qs.none()
        if documento_tipo:
            qs = qs.filter(documento_tipo=documento_tipo)
        if documento_id:
            qs = qs.filter(documento_id=documento_id) if documento_id.isdigit() else qs.none()

        export = request.GET.get('export')
        if export in {'csv', 'pdf'}:
            bloqueio = bloquear_exportacao_sem_permissao(request, 'estoque:movimentacao-list')
            if bloqueio:
                return bloqueio
            if export == 'pdf':
                return self._exportar_pdf(qs.order_by('-data_movimentacao'))
            return self._exportar_csv(qs.order_by('-data_movimentacao'))

        qs = qs.order_by('-data_movimentacao')
        page_obj = Paginator(qs, 50).get_page(request.GET.get('page'))
        movimentacoes = list(page_obj.object_list)
        ids_relacionados = [
            mov.documento_id
            for mov in movimentacoes
            if (
                mov.documento_tipo == MovimentacaoEstoque.DocumentoTipo.TRANSFERENCIA
                and mov.documento_id
            )
        ]
        ids_entradas_nf = [
            mov.documento_id
            for mov in movimentacoes
            if (
                mov.documento_tipo == MovimentacaoEstoque.DocumentoTipo.NFE
                and mov.documento_id
            )
        ]
        relacionados = {
            mov.pk: mov
            for mov in MovimentacaoEstoque.objects.filter(pk__in=ids_relacionados).select_related(
                'filial',
                'filial_destino',
            )
        }
        entradas_nf = {
            entrada.pk: entrada
            for entrada in EntradaNF.objects.for_filial(request.filial_ativa)
            .filter(pk__in=ids_entradas_nf)
            .only('pk', 'numero_nf', 'serie_nf')
        }
        for mov in movimentacoes:
            mov.movimento_relacionado = relacionados.get(mov.documento_id)
            mov.entrada_nf = entradas_nf.get(mov.documento_id)
            for chave, valor in EstoqueKardexProdutoView._dados_custo_movimento(mov).items():
                setattr(mov, chave, valor)

        querydict = request.GET.copy()
        querydict.pop('page', None)

        return render(request, self.template_name, {
            'page_obj': page_obj,
            'movimentacoes': movimentacoes,
            'busca': busca,
            'tipo': tipo,
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'produto_filtrado': produto_filtrado,
            'auditoria_produto': list(auditoria_relacionada(produto_filtrado, limit=12)) if produto_filtrado else [],
            'tipos': MovimentacaoEstoque.TipoOperacao.choices,
            'permissoes_estoque': permissoes_estoque(request),
            'page_querystring': querydict.urlencode(),
        })

    @staticmethod
    def _exportar_csv(qs):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="movimentacoes_estoque.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Data',
            'Tipo',
            'Produto ID',
            'Produto',
            'Lote',
            'Quantidade',
            'Saldo anterior',
            'Saldo apos',
            'Custo medio anterior',
            'Custo medio apos',
            'Custo unitario movimento',
            'Documento tipo',
            'Documento numero',
            'Documento ID',
            'Filial origem',
            'Filial destino',
            'Usuario',
            'Observacao',
        ])
        for mov in qs:
            filial_destino = ''
            if mov.filial_destino_id:
                filial_destino = mov.filial_destino.nome_fantasia or mov.filial_destino.razao_social
            writer.writerow([
                mov.data_movimentacao.strftime('%d/%m/%Y %H:%M') if mov.data_movimentacao else '',
                mov.get_tipo_operacao_display(),
                mov.produto.codigo_replicacao,
                mov.produto.descricao,
                mov.lote.numero_lote if mov.lote_id else '',
                mov.quantidade,
                mov.quantidade_anterior,
                mov.quantidade_posterior,
                mov.custo_medio_anterior or '',
                mov.custo_medio_posterior or '',
                mov.valor_unitario or '',
                mov.get_documento_tipo_display() if mov.documento_tipo else '',
                mov.documento_numero,
                mov.documento_id or '',
                mov.filial.nome_fantasia or mov.filial.razao_social,
                filial_destino,
                mov.usuario.nome if getattr(mov.usuario, 'nome', '') else str(mov.usuario),
                mov.observacao,
            ])
        return response

    @staticmethod
    def _exportar_pdf(qs):
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="movimentacoes_estoque.pdf"'
        doc = SimpleDocTemplate(
            response,
            pagesize=landscape(A4),
            rightMargin=18,
            leftMargin=18,
            topMargin=20,
            bottomMargin=20,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph('Movimentacoes de estoque', styles['Title']),
            Paragraph('Extrato com saldo apos e evolucao de custo medio.', styles['BodyText']),
            Spacer(1, 10),
        ]
        data = [[
            'Data',
            'Tipo',
            'Produto',
            'Qtd',
            'Saldo apos',
            'Custo medio',
            'Unit. mov.',
            'Documento',
            'Usuario',
        ]]
        for mov in qs:
            documento = mov.documento_numero or mov.get_documento_tipo_display() or '-'
            usuario = mov.usuario.nome if getattr(mov.usuario, 'nome', '') else str(mov.usuario)
            data.append([
                mov.data_movimentacao.strftime('%d/%m/%Y %H:%M') if mov.data_movimentacao else '',
                mov.get_tipo_operacao_display(),
                f'{mov.produto.codigo_replicacao} - {mov.produto.descricao}'[:48],
                EstoqueListView._formatar_quantidade(mov.quantidade),
                EstoqueListView._formatar_quantidade(mov.quantidade_posterior),
                EstoqueKardexProdutoView._formatar_moeda_opcional(mov.custo_medio_posterior),
                EstoqueKardexProdutoView._formatar_moeda_opcional(mov.valor_unitario),
                documento[:30],
                usuario[:22],
            ])
        table = Table(data, repeatRows=1, colWidths=[62, 74, 154, 42, 54, 66, 66, 86, 68])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
            ('TOPPADDING', (0, 0), (-1, 0), 7),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('ALIGN', (3, 1), (6, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return response
