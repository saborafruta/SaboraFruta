"""CRUD de lote."""
import csv
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.services.auditoria import auditoria_para_objeto, registrar_auditoria, snapshot_modelo
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.compras.models import ItemEntradaNF
from apps.estoque.forms import LoteProdutoForm
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.estoque.views.permissoes import (
    bloquear_exportacao_sem_permissao,
    permissoes_estoque,
)


def _auditar_lote(request, acao, lote, descricao='', justificativa='', antes=None, depois=None, relacionado=None, metadados=None):
    return registrar_auditoria(
        request=request,
        modulo='estoque',
        acao=acao,
        objeto=lote,
        descricao=descricao or f'Lote {lote.numero_lote}',
        justificativa=justificativa,
        antes=antes,
        depois=depois,
        relacionado=relacionado,
        metadados=metadados,
    )


class LoteListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/lote/list.html'

    def get(self, request):
        qs = LoteProduto.objects.for_filial(request.filial_ativa).select_related(
            'produto',
            'fornecedor',
        )

        busca = request.GET.get('q', '').strip()
        status = request.GET.get('status', '')
        vencendo = request.GET.get('vencendo', '')

        if busca:
            lotes_por_nf_origem = (
                ItemEntradaNF.objects
                .filter(
                    entrada__filial=request.filial_ativa,
                    entrada__numero_nf__icontains=busca,
                    lote_gerado__isnull=False,
                )
                .values('lote_gerado_id')
            )
            filtro_busca = (
                Q(numero_lote__icontains=busca)
                | Q(produto__codigo__icontains=busca)
                | Q(produto__codigo_barras__icontains=busca)
                | Q(produto__descricao__icontains=busca)
                | Q(numero_nota_entrada__icontains=busca)
                | Q(pk__in=lotes_por_nf_origem)
            )
            busca_codigo = busca.lstrip('0')
            if busca_codigo.isdigit():
                codigo_int = int(busca_codigo)
                filtro_busca |= Q(produto__pk=codigo_int) | Q(produto__id_externo=f'produto:{codigo_int}')
            qs = qs.filter(filtro_busca)
        if status:
            qs = qs.filter(status=status)

        if vencendo:
            from datetime import timedelta
            from django.utils import timezone
            hoje = timezone.localdate()
            dias = int(vencendo)
            qs = qs.filter(
                data_validade__isnull=False,
                data_validade__gte=hoje,
                data_validade__lte=hoje + timedelta(days=dias),
            )

        export = request.GET.get('export')
        if export in {'csv', 'pdf'}:
            bloqueio = bloquear_exportacao_sem_permissao(request, 'estoque:lote-list')
            if bloqueio:
                return bloqueio
            if export == 'pdf':
                return self._exportar_pdf(qs.order_by('data_validade', 'numero_lote'))
            return self._exportar_csv(qs.order_by('data_validade', 'numero_lote'))

        qs = qs.order_by('data_validade', 'numero_lote')
        page_obj = Paginator(qs, 30).get_page(request.GET.get('page'))
        lotes = list(page_obj.object_list)
        self._anexar_rastreio(lotes)
        querydict = request.GET.copy()
        querydict.pop('page', None)

        return render(request, self.template_name, {
            'page_obj': page_obj,
            'lotes': lotes,
            'busca': busca,
            'status': status,
            'vencendo': vencendo,
            'status_choices': LoteProduto.Status.choices,
            'permissoes_estoque': permissoes_estoque(request),
            'page_querystring': querydict.urlencode(),
        })

    @staticmethod
    def _anexar_rastreio(lotes):
        ids = [lote.pk for lote in lotes]
        if not ids:
            return
        itens_entrada = {}
        for item in (
            ItemEntradaNF.objects
            .filter(lote_gerado_id__in=ids)
            .select_related('entrada', 'entrada__fornecedor')
            .order_by('lote_gerado_id', '-entrada__data_entrada', '-pk')
        ):
            itens_entrada.setdefault(item.lote_gerado_id, item)
        movimentos = {
            row['lote_id']: row['total']
            for row in (
                MovimentacaoEstoque.objects
                .filter(lote_id__in=ids)
                .values('lote_id')
                .annotate(total=Count('id'))
            )
        }
        for lote in lotes:
            quantidade = lote.quantidade_atual or Decimal('0')
            custo = lote.custo_unitario or Decimal('0')
            lote.valor_total_lote = quantidade * custo
            lote.item_entrada_origem = itens_entrada.get(lote.pk)
            lote.movimentacoes_count = movimentos.get(lote.pk, 0)
            if lote.item_entrada_origem:
                entrada = lote.item_entrada_origem.entrada
                lote.nf_origem_label = f'NF {entrada.numero_nf}/{entrada.serie_nf}'
            elif lote.numero_nota_entrada:
                lote.nf_origem_label = f'NF {lote.numero_nota_entrada}'
            else:
                lote.nf_origem_label = '-'

    @staticmethod
    def _exportar_csv(qs):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="lotes_estoque.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Numero lote',
            'Produto ID',
            'Produto',
            'Fornecedor',
            'Fabricacao',
            'Validade',
            'Dias para vencer',
            'Quantidade atual',
            'Custo unitario',
            'Valor lote',
            'NF origem',
            'Status',
            'Motivo bloqueio',
        ])
        for lote in qs:
            item_entrada = (
                ItemEntradaNF.objects
                .filter(lote_gerado=lote)
                .select_related('entrada')
                .order_by('-entrada__data_entrada', '-pk')
                .first()
            )
            valor_total_lote = (lote.quantidade_atual or Decimal('0')) * (lote.custo_unitario or Decimal('0'))
            writer.writerow([
                lote.numero_lote,
                lote.produto.codigo_replicacao,
                lote.produto.descricao,
                lote.fornecedor.razao_social if lote.fornecedor_id else '',
                lote.data_fabricacao.strftime('%d/%m/%Y') if lote.data_fabricacao else '',
                lote.data_validade.strftime('%d/%m/%Y') if lote.data_validade else '',
                lote.dias_para_vencer if lote.data_validade else '',
                lote.quantidade_atual,
                lote.custo_unitario,
                valor_total_lote,
                (
                    f'{item_entrada.entrada.numero_nf}/{item_entrada.entrada.serie_nf}'
                    if item_entrada
                    else lote.numero_nota_entrada
                ),
                lote.get_status_display(),
                lote.motivo_bloqueio,
            ])
        return response

    @staticmethod
    def _formatar_moeda(valor):
        if valor is None:
            valor = Decimal('0')
        valor = Decimal(str(valor))
        return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

    @staticmethod
    def _exportar_pdf(qs):
        lotes = list(qs)
        LoteListView._anexar_rastreio(lotes)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="lotes_estoque.pdf"'
        doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=18, leftMargin=18)
        styles = getSampleStyleSheet()
        elements = [
            Paragraph('Lotes de estoque', styles['Title']),
            Paragraph('Rastreio por produto, validade, saldo, custo e origem fiscal.', styles['BodyText']),
            Spacer(1, 10),
        ]
        data = [[
            'Lote',
            'Produto',
            'Fornecedor',
            'Validade',
            'Qtd.',
            'Custo un.',
            'Valor lote',
            'Origem',
            'Status',
        ]]
        for lote in lotes:
            validade = lote.data_validade.strftime('%d/%m/%Y') if lote.data_validade else '-'
            data.append([
                lote.numero_lote[:18],
                lote.produto.descricao[:38],
                (lote.fornecedor.razao_social if lote.fornecedor_id else '-')[:28],
                validade,
                str(lote.quantidade_atual),
                LoteListView._formatar_moeda(lote.custo_unitario),
                LoteListView._formatar_moeda(lote.valor_total_lote),
                lote.nf_origem_label,
                lote.get_status_display(),
            ])
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f2937')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
        doc.build(elements)
        return response


class LoteCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'criar'
    template_name = 'estoque/lote/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': LoteProdutoForm(filial=request.filial_ativa),
            'title': 'Novo lote',
            'cancel_url': reverse_lazy('estoque:lote-list'),
        })

    def post(self, request):
        form = LoteProdutoForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                with transaction.atomic():
                    lote = form.save(commit=False)
                    lote.filial = request.filial_ativa
                    quantidade_inicial = lote.quantidade_inicial or Decimal('0')
                    lote.quantidade_atual = Decimal('0')
                    lote.save()
                    if quantidade_inicial > 0:
                        MovimentacaoService.registrar_movimentacao(
                            produto_id=lote.produto_id,
                            filial_id=request.filial_ativa.pk,
                            tipo_operacao=MovimentacaoEstoque.TipoOperacao.ENTRADA,
                            quantidade=quantidade_inicial,
                            usuario_id=request.user.pk,
                            lote_id=lote.pk,
                            valor_unitario=lote.custo_unitario,
                            documento_tipo=(
                                MovimentacaoEstoque.DocumentoTipo.NFE
                                if lote.numero_nota_entrada
                                else MovimentacaoEstoque.DocumentoTipo.OUTRAS
                            ),
                            documento_numero=lote.numero_nota_entrada,
                            observacao=f'Entrada inicial do lote {lote.numero_lote}.',
                        )
                _auditar_lote(
                    request,
                    'criar',
                    lote,
                    f'Lote {lote.numero_lote} criado',
                    depois=snapshot_modelo(lote),
                    relacionado=lote.produto,
                    metadados={'quantidade_inicial': str(quantidade_inicial)},
                )
                messages.success(request, f'Lote "{lote.numero_lote}" criado.')
                return redirect('estoque:lote-list')
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'title': 'Novo lote',
            'cancel_url': reverse_lazy('estoque:lote-list'),
        })


class LoteUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'editar'
    template_name = 'estoque/lote/form.html'

    def get(self, request, pk):
        lote = get_object_or_404(LoteProduto.objects.for_filial(request.filial_ativa), pk=pk)
        return render(request, self.template_name, {
            'form': LoteProdutoForm(instance=lote, filial=request.filial_ativa),
            'lote': lote,
            'auditoria_lote': list(auditoria_para_objeto(lote, limit=12)),
            'title': f'Editar lote - {lote.numero_lote}',
            'cancel_url': reverse_lazy('estoque:lote-list'),
        })

    def post(self, request, pk):
        lote = get_object_or_404(LoteProduto.objects.for_filial(request.filial_ativa), pk=pk)
        form = LoteProdutoForm(request.POST, instance=lote, filial=request.filial_ativa)
        if form.is_valid():
            antes = snapshot_modelo(lote)
            form.save()
            lote.refresh_from_db()
            _auditar_lote(
                request,
                'editar',
                lote,
                f'Lote {lote.numero_lote} alterado',
                justificativa=request.POST.get('motivo_bloqueio', '') or 'Alteracao cadastral do lote',
                antes=antes,
                depois=snapshot_modelo(lote),
                relacionado=lote.produto,
            )
            messages.success(request, 'Lote atualizado.')
            return redirect('estoque:lote-list')
        return render(request, self.template_name, {
            'form': form,
            'lote': lote,
            'auditoria_lote': list(auditoria_para_objeto(lote, limit=12)),
            'title': f'Editar lote - {lote.numero_lote}',
            'cancel_url': reverse_lazy('estoque:lote-list'),
        })


class LoteBaixaValidadeView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'cancelar'

    def post(self, request, pk):
        lote = get_object_or_404(LoteProduto.objects.for_filial(request.filial_ativa), pk=pk)
        try:
            antes = snapshot_modelo(lote)
            mov = MovimentacaoService.baixar_lote_por_validade(
                lote_id=lote.pk,
                usuario_id=request.user.pk,
            )
            lote.refresh_from_db()
            _auditar_lote(
                request,
                'baixar_validade',
                lote,
                f'Lote {lote.numero_lote} baixado por validade',
                justificativa=request.POST.get('justificativa', '') or 'Baixa por validade',
                antes=antes,
                depois=snapshot_modelo(lote),
                relacionado=mov,
                metadados={'movimentacao_id': mov.pk, 'quantidade_baixada': str(mov.quantidade)},
            )
            messages.success(request, f'Lote "{lote.numero_lote}" baixado por validade.')
        except DomainError as e:
            messages.error(request, str(e))
        return redirect('estoque:lote-list')
