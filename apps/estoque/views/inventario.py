"""Fluxo basico de inventario fisico."""
import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.tenant_context import tenant_atomic
from apps.core.services.auditoria import auditoria_para_objeto, registrar_auditoria, snapshot_modelo
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PERMISSION_DENIED_MESSAGE, PermissaoRequiredMixin
from apps.estoque.forms import InventarioForm
from apps.estoque.models import (
    Deposito, Estoque, Inventario, ItemInventario, LoteProduto, MovimentacaoEstoque,
)
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.permissoes import (
    bloquear_exportacao_sem_permissao,
    permissoes_estoque,
)
from apps.produtos.models import Produto


def _auditar_inventario(request, acao, inventario, descricao='', justificativa='', antes=None, depois=None, relacionado=None, metadados=None):
    return registrar_auditoria(
        request=request,
        modulo='estoque',
        acao=acao,
        objeto=inventario,
        descricao=descricao or f'Inventario #{inventario.pk}',
        justificativa=justificativa,
        antes=antes,
        depois=depois,
        relacionado=relacionado,
        metadados=metadados,
    )


def _decimal_from_request(value):
    if value is None or str(value).strip() == '':
        return None
    text = str(value).strip()
    if ',' in text and '.' in text:
        text = text.replace('.', '').replace(',', '.')
    elif ',' in text:
        text = text.replace(',', '.')
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        raise DomainError('Quantidade contada invalida.')


def _valor_diferenca(diferenca, valor_unitario):
    if diferenca is None or valor_unitario is None:
        return None
    return (diferenca * valor_unitario).quantize(Decimal('0.01'))


def _resumo_inventario(itens):
    total = len(itens)
    contados = 0
    divergentes = 0
    valor_sobra = Decimal('0')
    valor_falta = Decimal('0')
    valor_liquido = Decimal('0')

    for item in itens:
        if item.quantidade_contada is not None:
            contados += 1
        diferenca = item.diferenca
        if diferenca is None or diferenca == 0:
            continue
        divergentes += 1
        valor = item.valor_diferenca or Decimal('0')
        valor_liquido += valor
        if diferenca > 0:
            valor_sobra += abs(valor)
        else:
            valor_falta += abs(valor)

    pendentes = total - contados
    progresso = int((contados / total) * 100) if total else 0
    return {
        'total': total,
        'contados': contados,
        'pendentes': pendentes,
        'divergentes': divergentes,
        'sem_divergencia': contados - divergentes,
        'valor_sobra': valor_sobra,
        'valor_falta': valor_falta,
        'valor_liquido': valor_liquido,
        'progresso_percentual': progresso,
    }


def _relatorio_divergencias(itens):
    faltas = 0
    sobras = 0
    quantidade_falta = Decimal('0')
    quantidade_sobra = Decimal('0')
    valor_falta = Decimal('0')
    valor_sobra = Decimal('0')

    for item in itens:
        diferenca = item.diferenca or Decimal('0')
        valor = item.valor_diferenca or Decimal('0')
        item.valor_diferenca_absoluto = abs(valor)
        if diferenca < 0:
            faltas += 1
            quantidade_falta += abs(diferenca)
            valor_falta += abs(valor)
            item.tipo_divergencia = 'Falta'
        elif diferenca > 0:
            sobras += 1
            quantidade_sobra += diferenca
            valor_sobra += abs(valor)
            item.tipo_divergencia = 'Sobra'
        else:
            item.tipo_divergencia = ''

    return {
        'faltas': faltas,
        'sobras': sobras,
        'quantidade_falta': quantidade_falta,
        'quantidade_sobra': quantidade_sobra,
        'valor_falta': valor_falta,
        'valor_sobra': valor_sobra,
        'valor_total': valor_falta + valor_sobra,
        'valor_liquido': valor_sobra - valor_falta,
        'itens': faltas + sobras,
    }


def _criar_itens_inventario(inventario, _filial=None):
    # `_filial` fica só por compatibilidade com chamadas antigas — a filial
    # e o depósito autoritativos vêm do próprio inventário.
    filial = inventario.filial
    deposito_id = inventario.deposito_id
    conta_lotes = deposito_id == Deposito.padrao_id(filial.pk)
    produtos = list(
        Produto.objects.for_filial(filial).filter(ativo=True).select_related(
            'unidade_medida',
            'categoria',
        ).order_by('descricao')
    )
    estoques = {
        item.produto_id: item
        for item in Estoque.objects.filter(
            filial=filial,
            deposito_id=deposito_id,
            produto_id__in=[produto.pk for produto in produtos],
        )
    }
    itens = []
    for produto in produtos:
        if produto.controla_lote:
            # O saldo do lote é da filial, não do depósito. Para não contar o
            # mesmo lote em dois inventários, só o inventário do depósito
            # padrão traz os produtos com lote.
            if not conta_lotes:
                continue
            lotes = LoteProduto.objects.filter(
                filial=filial,
                produto=produto,
                quantidade_atual__gt=0,
            ).order_by('data_validade', 'numero_lote')
            for lote in lotes:
                itens.append(ItemInventario(
                    inventario=inventario,
                    produto=produto,
                    lote=lote,
                    quantidade_sistema=lote.quantidade_atual,
                    valor_unitario=lote.custo_unitario,
                ))
            continue
        estoque = estoques.get(produto.pk)
        quantidade_sistema = estoque.quantidade_atual if estoque else Decimal('0')
        valor_unitario = estoque.custo_medio if estoque else Decimal('0')
        itens.append(ItemInventario(
            inventario=inventario,
            produto=produto,
            quantidade_sistema=quantidade_sistema,
            valor_unitario=valor_unitario,
        ))
    if itens:
        ItemInventario.objects.bulk_create(itens, batch_size=500)


class InventarioListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/inventario/list.html'

    def get(self, request):
        qs = Inventario.objects.for_filial(request.filial_ativa).select_related(
            'usuario_inicio',
            'usuario_fechamento',
            'deposito',
        ).annotate(
            itens_total=Count('itens', distinct=True),
            divergencias_total=Count(
                'itens',
                filter=Q(itens__diferenca__isnull=False) & ~Q(itens__diferenca=0),
                distinct=True,
            ),
        )

        status = request.GET.get('status', '')
        if status:
            qs = qs.filter(status=status)

        page_obj = Paginator(qs.order_by('-data_inicio'), 30).get_page(request.GET.get('page'))
        querydict = request.GET.copy()
        querydict.pop('page', None)
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'inventarios': page_obj.object_list,
            'status': status,
            'status_choices': Inventario.Status.choices,
            'permissoes_estoque': permissoes_estoque(request),
            'page_querystring': querydict.urlencode(),
        })


class InventarioCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'criar'
    template_name = 'estoque/inventario/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': InventarioForm(filial=request.filial_ativa),
            'title': 'Novo inventario',
            'cancel_url': reverse_lazy('estoque:inventario-list'),
        })

    def post(self, request):
        form = InventarioForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            with tenant_atomic():
                inventario = form.save(commit=False)
                inventario.filial = request.filial_ativa
                inventario.usuario_inicio = request.user
                inventario.data_inicio = timezone.now()
                inventario.status = Inventario.Status.ABERTO
                inventario.save()
                _criar_itens_inventario(inventario)
            _auditar_inventario(
                request,
                'criar',
                inventario,
                f'Inventario #{inventario.pk} aberto',
                justificativa=inventario.observacao,
                depois=snapshot_modelo(inventario),
                metadados={'itens': inventario.itens.count()},
            )
            messages.success(request, 'Inventario aberto com snapshot dos produtos da filial.')
            return redirect('estoque:inventario-detail', pk=inventario.pk)
        return render(request, self.template_name, {
            'form': form,
            'title': 'Novo inventario',
            'cancel_url': reverse_lazy('estoque:inventario-list'),
        })


class InventarioDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/inventario/detail.html'

    def get_inventario(self, request, pk):
        return get_object_or_404(
            Inventario.objects.for_filial(request.filial_ativa),
            pk=pk,
        )

    def get(self, request, pk):
        inventario = self.get_inventario(request, pk)
        itens = list(
            inventario.itens.select_related('produto', 'produto__unidade_medida', 'lote').order_by(
                'produto__descricao',
                'lote__data_validade',
                'lote__numero_lote',
            )
        )
        if request.GET.get('export') == 'csv':
            bloqueio = bloquear_exportacao_sem_permissao(
                request,
                'estoque:inventario-detail',
                pk=pk,
            )
            if bloqueio:
                return bloqueio
            return self._exportar_csv(inventario, itens)
        return render(request, self.template_name, {
            'inventario': inventario,
            'itens': itens,
            'resumo': _resumo_inventario(itens),
            'permissoes_estoque': permissoes_estoque(request),
            'auditoria_inventario': list(auditoria_para_objeto(inventario, limit=12)),
        })

    def post(self, request, pk):
        permissoes = permissoes_estoque(request)
        if not permissoes['pode_contar_inventario']:
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('estoque:inventario-detail', pk=pk)
        if request.POST.get('acao') == 'fechar' and not permissoes['pode_fechar_inventario']:
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('estoque:inventario-detail', pk=pk)

        inventario = self.get_inventario(request, pk)
        if inventario.status in {Inventario.Status.FECHADO, Inventario.Status.CANCELADO}:
            messages.error(request, 'Inventario fechado ou cancelado nao pode ser alterado.')
            return redirect('estoque:inventario-detail', pk=inventario.pk)

        try:
            antes_inventario = snapshot_modelo(inventario)
            itens_alterados = 0
            with tenant_atomic():
                itens = list(inventario.itens.select_related('produto').order_by('produto__descricao'))
                for item in itens:
                    quantidade = _decimal_from_request(request.POST.get(f'quantidade_contada_{item.pk}'))
                    justificativa = request.POST.get(f'justificativa_{item.pk}', '').strip()
                    if quantidade is None:
                        continue
                    antes_item = snapshot_modelo(item)
                    item.quantidade_contada = quantidade
                    item.diferenca = quantidade - item.quantidade_sistema
                    item.valor_diferenca = _valor_diferenca(item.diferenca, item.valor_unitario)
                    item.justificativa = justificativa
                    item.usuario_contagem = request.user
                    item.data_contagem = timezone.now()
                    item.save(update_fields=[
                        'quantidade_contada',
                        'diferenca',
                        'valor_diferenca',
                        'justificativa',
                        'usuario_contagem',
                        'data_contagem',
                        'updated_at',
                    ])
                    itens_alterados += 1
                    registrar_auditoria(
                        request=request,
                        modulo='estoque',
                        acao='inventariar',
                        objeto=item,
                        descricao=f'Contagem do item {item.produto.descricao}',
                        justificativa=justificativa,
                        antes=antes_item,
                        depois=snapshot_modelo(item),
                        relacionado=inventario,
                        metadados={'inventario_id': inventario.pk, 'diferenca': str(item.diferenca or '0')},
                    )

                if request.POST.get('acao') == 'fechar':
                    self._fechar_inventario(request, inventario)
                    inventario.refresh_from_db()
                    _auditar_inventario(
                        request,
                        'aprovar',
                        inventario,
                        f'Inventario #{inventario.pk} fechado',
                        justificativa=inventario.observacao or 'Fechamento de inventario',
                        antes=antes_inventario,
                        depois=snapshot_modelo(inventario),
                        metadados={'itens_contados': itens_alterados},
                    )
                    messages.success(request, 'Inventario fechado e ajustes gerados.')
                    return redirect('estoque:inventario-detail', pk=inventario.pk)

                inventario.status = Inventario.Status.EM_CONTAGEM
                inventario.save(update_fields=['status', 'updated_at'])
                inventario.refresh_from_db()
                _auditar_inventario(
                    request,
                    'inventariar',
                    inventario,
                    f'Contagem salva no inventario #{inventario.pk}',
                    justificativa=inventario.observacao or 'Contagem de inventario',
                    antes=antes_inventario,
                    depois=snapshot_modelo(inventario),
                    metadados={'itens_alterados': itens_alterados},
                )
            messages.success(request, 'Contagem salva.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('estoque:inventario-detail', pk=inventario.pk)

    def _fechar_inventario(self, request, inventario):
        itens = list(inventario.itens.select_related('produto'))
        itens_sem_contagem = [item for item in itens if item.quantidade_contada is None]
        if itens_sem_contagem:
            raise DomainError('Informe a quantidade contada de todos os itens antes de fechar.')

        for item in itens:
            if not item.diferenca:
                continue
            MovimentacaoService.ajustar_manual(
                produto_id=item.produto_id,
                filial_id=inventario.filial_id,
                quantidade_nova=item.quantidade_contada,
                usuario_id=request.user.pk,
                justificativa=(
                    f'Inventario #{inventario.pk}. '
                    f'{item.justificativa}'.strip()
                ),
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.INVENTARIO,
                documento_id=inventario.pk,
                documento_numero=str(inventario.pk),
                lote_id=item.lote_id,
                deposito_id=inventario.deposito_id,
            )

        inventario.status = Inventario.Status.FECHADO
        inventario.usuario_fechamento = request.user
        inventario.data_fim = timezone.now()
        inventario.save(update_fields=[
            'status',
            'usuario_fechamento',
            'data_fim',
            'updated_at',
        ])

    @staticmethod
    def _exportar_csv(inventario, itens):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="inventario_{inventario.pk}.csv"'
        )
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Inventario',
            inventario.pk,
            inventario.descricao,
            inventario.get_status_display(),
        ])
        writer.writerow([
            'Produto ID',
            'Codigo',
            'Produto',
            'Lote',
            'Unidade',
            'Quantidade sistema',
            'Quantidade contada',
            'Diferenca',
            'Valor unitario',
            'Valor diferenca',
            'Justificativa',
        ])
        for item in itens:
            produto = item.produto
            writer.writerow([
                produto.codigo_replicacao,
                produto.codigo,
                produto.descricao,
                item.lote.numero_lote if item.lote_id else '',
                produto.unidade_medida.sigla if produto.unidade_medida_id else '',
                item.quantidade_sistema,
                item.quantidade_contada if item.quantidade_contada is not None else '',
                item.diferenca if item.diferenca is not None else '',
                item.valor_unitario if item.valor_unitario is not None else '',
                item.valor_diferenca if item.valor_diferenca is not None else '',
                item.justificativa,
            ])
        return response


class InventarioDivergenciasView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/inventario/divergencias.html'

    def get(self, request, pk):
        inventario = get_object_or_404(
            Inventario.objects.for_filial(request.filial_ativa),
            pk=pk,
        )
        itens = list(
            inventario.itens.select_related('produto', 'produto__unidade_medida', 'lote').filter(
                diferenca__isnull=False,
            ).exclude(
                diferenca=0,
            ).order_by('produto__descricao', 'lote__data_validade', 'lote__numero_lote')
        )
        export = request.GET.get('export')
        if export in {'csv', 'pdf'}:
            bloqueio = bloquear_exportacao_sem_permissao(
                request,
                'estoque:inventario-divergencias',
                pk=pk,
            )
            if bloqueio:
                return bloqueio
            if export == 'pdf':
                return self._exportar_pdf(inventario, itens)
            return self._exportar_csv(inventario, itens)
        relatorio = _relatorio_divergencias(itens)
        return render(request, self.template_name, {
            'inventario': inventario,
            'itens': itens,
            'resumo': _resumo_inventario(list(inventario.itens.all())),
            'relatorio': relatorio,
            'permissoes_estoque': permissoes_estoque(request),
        })

    @staticmethod
    def _exportar_csv(inventario, itens):
        _relatorio_divergencias(itens)
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = (
            f'attachment; filename="inventario_{inventario.pk}_divergencias.csv"'
        )
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Inventario',
            inventario.pk,
            inventario.descricao,
            inventario.get_status_display(),
        ])
        writer.writerow([
            'Produto ID',
            'Codigo',
            'Produto',
            'Lote',
            'Unidade',
            'Tipo divergencia',
            'Quantidade sistema',
            'Quantidade contada',
            'Diferenca',
            'Valor unitario',
            'Impacto',
            'Justificativa',
        ])
        for item in itens:
            produto = item.produto
            writer.writerow([
                produto.codigo_replicacao,
                produto.codigo,
                produto.descricao,
                item.lote.numero_lote if item.lote_id else '',
                produto.unidade_medida.sigla if produto.unidade_medida_id else '',
                item.tipo_divergencia,
                item.quantidade_sistema,
                item.quantidade_contada if item.quantidade_contada is not None else '',
                item.diferenca,
                item.valor_unitario if item.valor_unitario is not None else '',
                item.valor_diferenca_absoluto,
                item.justificativa,
            ])
        return response

    @staticmethod
    def _formatar_moeda(valor):
        if valor is None:
            valor = Decimal('0')
        valor = Decimal(str(valor))
        return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    @staticmethod
    def _exportar_pdf(inventario, itens):
        relatorio = _relatorio_divergencias(itens)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="inventario_{inventario.pk}_divergencias.pdf"'
        )
        doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=18, leftMargin=18)
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(f'Divergencias do inventario #{inventario.pk}', styles['Title']),
            Paragraph(
                (
                    f'{inventario.descricao or "Inventario fechado"} - '
                    f'{relatorio["itens"]} item(ns), '
                    f'faltas {InventarioDivergenciasView._formatar_moeda(relatorio["valor_falta"])}, '
                    f'sobras {InventarioDivergenciasView._formatar_moeda(relatorio["valor_sobra"])}.'
                ),
                styles['BodyText'],
            ),
            Spacer(1, 10),
        ]
        data = [[
            'Produto',
            'Lote',
            'Tipo',
            'Sistema',
            'Contado',
            'Dif.',
            'Valor un.',
            'Impacto',
            'Justificativa',
        ]]
        for item in itens:
            data.append([
                item.produto.descricao[:38],
                item.lote.numero_lote if item.lote_id else '-',
                item.tipo_divergencia,
                str(item.quantidade_sistema),
                str(item.quantidade_contada),
                str(item.diferenca),
                InventarioDivergenciasView._formatar_moeda(item.valor_unitario),
                InventarioDivergenciasView._formatar_moeda(item.valor_diferenca_absoluto),
                item.justificativa[:36],
            ])
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (3, 1), (7, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return response


class InventarioCancelView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'cancelar'

    def post(self, request, pk):
        inventario = get_object_or_404(
            Inventario.objects.for_filial(request.filial_ativa),
            pk=pk,
        )
        if inventario.status == Inventario.Status.FECHADO:
            messages.error(request, 'Inventario fechado nao pode ser cancelado.')
            return redirect('estoque:inventario-detail', pk=inventario.pk)
        motivo = request.POST.get('motivo', '').strip() or request.POST.get('justificativa', '').strip()
        if not motivo:
            messages.error(request, 'Informe a justificativa para cancelar o inventario.')
            return redirect('estoque:inventario-detail', pk=inventario.pk)
        antes = snapshot_modelo(inventario)
        inventario.status = Inventario.Status.CANCELADO
        inventario.data_fim = timezone.now()
        inventario.usuario_fechamento = request.user
        inventario.save(update_fields=[
            'status',
            'data_fim',
            'usuario_fechamento',
            'updated_at',
        ])
        _auditar_inventario(
            request,
            'cancelar',
            inventario,
            f'Inventario #{inventario.pk} cancelado',
            justificativa=motivo,
            antes=antes,
            depois=snapshot_modelo(inventario),
        )
        messages.success(request, 'Inventario cancelado.')
        return redirect('estoque:inventario-list')
