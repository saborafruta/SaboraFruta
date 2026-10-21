import csv
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import Deposito, Estoque
from apps.estoque.views.permissoes import bloquear_exportacao_sem_permissao


class RelatorioEstoqueLocalizacaoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'estoque/relatorios/localizacoes.html'

    def _dados(self, request):
        busca = request.GET.get('q', '').strip()
        deposito_id = request.GET.get('deposito', '').strip()
        somente_com_saldo = request.GET.get('com_saldo', '1') == '1'
        estoques = (
            Estoque.objects.filter(filial=request.filial_ativa)
            .select_related('produto', 'produto__unidade_medida', 'deposito')
            .prefetch_related('saldos_enderecos__endereco')
            .order_by('deposito__nome', 'produto__descricao', 'produto_id')
        )
        if deposito_id.isdigit():
            estoques = estoques.filter(deposito_id=int(deposito_id))
        if busca:
            estoques = estoques.filter(
                Q(produto__descricao__icontains=busca)
                | Q(produto__codigo__icontains=busca)
                | Q(produto__codigo_barras__icontains=busca)
                | Q(saldos_enderecos__endereco__codigo__icontains=busca)
                | Q(saldos_enderecos__endereco__descricao__icontains=busca)
            ).distinct()
        if somente_com_saldo:
            estoques = estoques.filter(quantidade_atual__gt=0)

        linhas = []
        total_estoque = Decimal('0')
        total_enderecado = Decimal('0')
        for estoque in estoques:
            saldos = sorted(
                [item for item in estoque.saldos_enderecos.all() if item.endereco.ativo],
                key=lambda item: (
                    item.prioridade_separacao, item.endereco.codigo, item.pk,
                ),
            )
            enderecado = sum((item.quantidade for item in saldos), Decimal('0'))
            total_estoque += estoque.quantidade_atual or Decimal('0')
            total_enderecado += enderecado
            for item in saldos:
                if somente_com_saldo and item.quantidade <= 0:
                    continue
                linhas.append({
                    'produto': estoque.produto,
                    'deposito': estoque.deposito,
                    'codigo': item.endereco.codigo,
                    'descricao': item.endereco.descricao,
                    'quantidade': item.quantidade,
                    'prioridade': item.prioridade_separacao,
                    'nao_enderecado': False,
                })
            sem_endereco = max(Decimal('0'), (estoque.quantidade_atual or Decimal('0')) - enderecado)
            if sem_endereco > 0:
                linhas.append({
                    'produto': estoque.produto,
                    'deposito': estoque.deposito,
                    'codigo': 'SEM ENDERECO',
                    'descricao': 'Saldo ainda nao distribuido em uma localizacao fisica.',
                    'quantidade': sem_endereco,
                    'prioridade': None,
                    'nao_enderecado': True,
                })
        return {
            'linhas': linhas,
            'depositos': Deposito.objects.filter(
                filial=request.filial_ativa, ativo=True,
            ).order_by('-is_padrao', 'nome'),
            'busca': busca,
            'deposito_id': deposito_id,
            'somente_com_saldo': somente_com_saldo,
            'total_estoque': total_estoque,
            'total_enderecado': total_enderecado,
            'total_nao_enderecado': max(Decimal('0'), total_estoque - total_enderecado),
        }

    def get(self, request):
        export = request.GET.get('export')
        if export in {'csv', 'pdf'}:
            bloqueio = bloquear_exportacao_sem_permissao(
                request, 'estoque:relatorio-localizacoes',
            )
            if bloqueio:
                return bloqueio
        contexto = self._dados(request)
        if export == 'csv':
            return self._csv(contexto)
        if export == 'pdf':
            return self._pdf(contexto)
        contexto['page_obj'] = Paginator(contexto['linhas'], 80).get_page(
            request.GET.get('page'),
        )
        filtros = request.GET.copy()
        filtros.pop('page', None)
        filtros.pop('export', None)
        contexto['filtros_query'] = filtros.urlencode()
        return render(request, self.template_name, contexto)

    @staticmethod
    def _csv(contexto):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="estoque-por-localizacao.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Produto', 'Codigo', 'Deposito', 'Localizacao', 'Descricao', 'Prioridade', 'Quantidade', 'Unidade'])
        for linha in contexto['linhas']:
            writer.writerow([
                linha['produto'].descricao,
                linha['produto'].codigo or linha['produto'].codigo_barras or linha['produto'].pk,
                linha['deposito'].nome,
                linha['codigo'],
                linha['descricao'],
                linha['prioridade'] or '',
                linha['quantidade'],
                linha['produto'].unidade_medida.sigla,
            ])
        return response

    @staticmethod
    def _pdf(contexto):
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="estoque-por-localizacao.pdf"'
        doc = SimpleDocTemplate(
            response, pagesize=landscape(A4), rightMargin=20, leftMargin=20,
            topMargin=24, bottomMargin=24,
        )
        styles = getSampleStyleSheet()
        elementos = [
            Paragraph('Estoque por localizacao', styles['Title']),
            Spacer(1, 8),
            Paragraph(
                f"Total {contexto['total_estoque']:.3f} | "
                f"Enderecado {contexto['total_enderecado']:.3f} | "
                f"Sem endereco {contexto['total_nao_enderecado']:.3f}",
                styles['Normal'],
            ),
            Spacer(1, 12),
        ]
        dados = [['Produto', 'Deposito', 'Localizacao', 'Descricao', 'Prior.', 'Qtd.', 'Un.']]
        for linha in contexto['linhas']:
            dados.append([
                linha['produto'].descricao,
                linha['deposito'].nome,
                linha['codigo'],
                linha['descricao'],
                linha['prioridade'] or '-',
                f"{linha['quantidade']:.3f}",
                linha['produto'].unidade_medida.sigla,
            ])
        tabela = Table(dados, repeatRows=1, colWidths=[175, 100, 120, 205, 42, 58, 35])
        tabela.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), .25, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elementos.append(tabela)
        doc.build(elementos)
        return response
