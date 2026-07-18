"""Dashboard principal — KPIs do dia, estoque por filial e RFM de clientes."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum
from django.utils import timezone
from django.views.generic import TemplateView

import datetime


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filial = getattr(self.request, 'filial_ativa', None)

        ctx['filial'] = filial
        ctx['kpis'] = self._calcular_kpis(filial)
        ctx['alertas'] = self._coletar_alertas(filial)
        ctx['estoque_filiais'] = self._estoque_por_filiais(filial)
        ctx['rfm_segmentos'] = self._calcular_rfm(filial)
        ctx['abc_clientes'] = self._curva_abc_clientes(filial)
        ctx['abc_produtos'] = self._curva_abc_produtos(filial)
        ctx['vendas_dia'] = self._vendas_periodo(filial, 'dia')
        ctx['vendas_mes'] = self._vendas_periodo(filial, 'mes')
        ctx['vendas_acumuladas'] = self._vendas_acumuladas_mes(filial)
        ctx['vendas_dow'] = self._vendas_por_dia_semana(filial)
        return ctx

    # ------------------------------------------------------------------
    # KPIs básicos
    # ------------------------------------------------------------------

    def _calcular_kpis(self, filial):
        if not filial:
            return {}

        from apps.estoque.models import Estoque
        from apps.produtos.models import Produto

        total_produtos = Produto.objects.for_filial(filial).filter(ativo=True).count()
        produtos_criticos = Estoque.objects.filter(
            filial=filial,
            quantidade_disponivel__lt=F('produto__estoque_minimo'),
            produto__ativo=True,
        ).count()

        return {
            'total_produtos': total_produtos,
            'produtos_criticos': produtos_criticos,
        }

    def _coletar_alertas(self, filial):
        if not filial:
            return []
        try:
            from apps.estoque.models import AlertaVencimento
            return list(
                AlertaVencimento.objects.filter(
                    filial=filial, resolvido=False,
                ).select_related('produto', 'lote').order_by('data_validade')[:10]
            )
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Estoque por filial
    # ------------------------------------------------------------------

    def _estoque_por_filiais(self, filial):
        """
        Retorna lista de dicts com resumo de estoque por filial.
        Se a filial ativa é matriz, mostra todas as filiais da empresa.
        Caso contrário, mostra apenas a filial ativa.
        """
        if not filial:
            return []

        try:
            from apps.core.models import Filial
            from apps.estoque.models import Estoque

            if filial.is_matriz:
                filiais_ids = list(
                    Filial.objects.filter(empresa=filial.empresa, ativo=True)
                    .values_list('id', flat=True)
                )
            else:
                filiais_ids = [filial.id]

            filiais_qs = Filial.objects.filter(id__in=filiais_ids).order_by(
                '-is_matriz', 'nome_fantasia', 'razao_social'
            )

            resultado = []
            for f in filiais_qs:
                valor_expr = ExpressionWrapper(
                    F('quantidade_atual') * F('custo_medio'),
                    output_field=DecimalField(max_digits=18, decimal_places=4),
                )
                agg = Estoque.objects.filter(filial=f, produto__ativo=True).aggregate(
                    total_itens=Count('id'),
                    qtd_total=Sum('quantidade_atual'),
                    qtd_disponivel=Sum('quantidade_disponivel'),
                    qtd_reservada=Sum('quantidade_reservada'),
                    valor_estoque=Sum(valor_expr),
                    criticos=Count(
                        'id',
                        filter=Q(quantidade_disponivel__lt=F('produto__estoque_minimo')),
                    ),
                )
                resultado.append({
                    'filial': f,
                    'is_matriz': f.is_matriz,
                    'total_itens': agg['total_itens'] or 0,
                    'qtd_total': agg['qtd_total'] or 0,
                    'qtd_disponivel': agg['qtd_disponivel'] or 0,
                    'qtd_reservada': agg['qtd_reservada'] or 0,
                    'valor_estoque': agg['valor_estoque'] or 0,
                    'criticos': agg['criticos'] or 0,
                })
            return resultado
        except Exception:
            return []

    # ------------------------------------------------------------------
    # RFM de clientes
    # ------------------------------------------------------------------

    # Definição dos segmentos — sempre retornados, mesmo sem dados
    _RFM_DEFINICOES = [
        {'id': 'campeoes',        'label': 'Campeões',            'descricao': 'Compram sempre, muito e recentemente',         'cor_bg': '#f87171', 'cor_texto': '#fff'},
        {'id': 'vips',            'label': 'VIPs',                'descricao': 'Alto valor, compras frequentes',                'cor_bg': '#fb923c', 'cor_texto': '#fff'},
        {'id': 'potenciais',      'label': 'Potenciais',          'descricao': 'Clientes recentes com bom ticket',              'cor_bg': '#4ade80', 'cor_texto': '#166534'},
        {'id': 'recentes',        'label': 'Recentes',            'descricao': 'Compraram recentemente, frequência baixa',      'cor_bg': '#60a5fa', 'cor_texto': '#1e3a5f'},
        {'id': 'promissores',     'label': 'Promissores',         'descricao': 'Recentes, ticket médio razoável',               'cor_bg': '#a3e635', 'cor_texto': '#365314'},
        {'id': 'precisam_atencao','label': 'Precisam de atenção', 'descricao': 'Valores medianos em todas as dimensões',        'cor_bg': '#facc15', 'cor_texto': '#713f12'},
        {'id': 'prestes_dormir',  'label': 'Prestes a dormir',    'descricao': 'Baixa recência e frequência',                   'cor_bg': '#d4d4aa', 'cor_texto': '#5a5a30'},
        {'id': 'nao_perder',      'label': 'Não perder',          'descricao': 'Alto valor histórico mas sumidos',              'cor_bg': '#86efac', 'cor_texto': '#14532d'},
        {'id': 'em_risco',        'label': 'Em risco',            'descricao': 'Bons clientes que pararam de comprar',          'cor_bg': '#93c5fd', 'cor_texto': '#1e3a5f'},
        {'id': 'hibernando',      'label': 'Hibernando',          'descricao': 'Baixa atividade há muito tempo',                'cor_bg': '#cbd5e1', 'cor_texto': '#475569'},
        {'id': 'perdidos',        'label': 'Perdidos',            'descricao': 'Sem atividade, baixo valor histórico',          'cor_bg': '#fca5a5', 'cor_texto': '#7f1d1d'},
    ]

    def _calcular_rfm(self, filial):
        """
        Calcula segmentos RFM dos clientes com base nos últimos 12 meses de vendas.
        Sempre retorna a lista de segmentos; sem dados, todas as quantidades ficam em 0.
        """
        contagem = {}
        clientes_segmento = {}
        total = 0
        erro = None

        if filial:
            try:
                from apps.vendas.models import PedidoVenda
                from apps.pdv.models import VendaPDV
                from apps.cadastros.models import Cliente

                hoje = timezone.now().date()
                inicio = hoje - datetime.timedelta(days=365)

                status_validos = [
                    PedidoVenda.Status.CONFIRMADO,
                    PedidoVenda.Status.EM_SEPARACAO,
                    PedidoVenda.Status.FATURADO,
                    PedidoVenda.Status.PARCIALMENTE_FATURADO,
                    PedidoVenda.Status.ENTREGUE,
                ]

                base_qs = (
                    PedidoVenda.objects.filter(filial__empresa=filial.empresa)
                    if filial.is_matriz
                    else PedidoVenda.objects.filter(filial=filial)
                )

                b2b_rows = list(
                    base_qs.filter(
                        status__in=status_validos,
                        data_emissao__date__gte=inicio,
                        cliente_id__isnull=False,
                    )
                    .values('cliente_id')
                    .annotate(
                        ultima_compra=Max('data_emissao'),
                        frequencia=Count('id'),
                        monetario=Sum('valor_total'),
                    )
                )

                pdv_rows = list(
                    (
                        VendaPDV.objects.filter(filial__empresa=filial.empresa)
                        if filial.is_matriz
                        else VendaPDV.objects.filter(filial=filial)
                    )
                    .filter(status='finalizada', cliente_id__isnull=False, data_venda__date__gte=inicio)
                    .values('cliente_id')
                    .annotate(
                        ultima_compra=Max('data_venda'),
                        frequencia=Count('id'),
                        monetario=Sum('valor_total'),
                    )
                )

                # Combina B2B + PDV por cliente_id
                acum_rfm = {}
                for row in b2b_rows + pdv_rows:
                    cid = row['cliente_id']
                    if cid not in acum_rfm:
                        acum_rfm[cid] = {'cliente_id': cid, 'ultima_compra': row['ultima_compra'],
                                         'frequencia': 0, 'monetario': 0}
                    acum_rfm[cid]['frequencia'] += row['frequencia']
                    acum_rfm[cid]['monetario']  += float(row['monetario'] or 0)
                    if row['ultima_compra'] and row['ultima_compra'] > acum_rfm[cid]['ultima_compra']:
                        acum_rfm[cid]['ultima_compra'] = row['ultima_compra']

                clientes = list(acum_rfm.values())

                if clientes:
                    cliente_ids = [c['cliente_id'] for c in clientes]
                    clientes_info = {
                        c.id: c for c in Cliente.objects.filter(id__in=cliente_ids).only(
                            'id', 'razao_social', 'nome_fantasia', 'cpf_cnpj'
                        )
                    }

                    for c in clientes:
                        ultima = c['ultima_compra']
                        if hasattr(ultima, 'date'):
                            ultima = ultima.date()
                        c['recencia_dias'] = (hoje - ultima).days

                    def quintil_score(valores, inverso=False):
                        sv = sorted(valores)
                        n = len(sv)
                        limites = [sv[max(0, int(n * p / 5) - 1)] for p in range(1, 6)]

                        def score(v):
                            for i, lim in enumerate(limites):
                                if v <= lim:
                                    s = i + 1
                                    return (6 - s) if inverso else s
                            return 1 if inverso else 5

                        return score

                    r_score = quintil_score([c['recencia_dias'] for c in clientes], inverso=True)
                    f_score = quintil_score([c['frequencia'] for c in clientes])
                    m_score = quintil_score([float(c['monetario'] or 0) for c in clientes])

                    for c in clientes:
                        c['R'] = r_score(c['recencia_dias'])
                        c['F'] = f_score(c['frequencia'])
                        c['M'] = m_score(float(c['monetario'] or 0))
                        seg = self._segmento_rfm(c['R'], c['F'], c['M'])
                        contagem[seg] = contagem.get(seg, 0) + 1

                        cliente = clientes_info.get(c['cliente_id'])
                        nome = (getattr(cliente, 'nome_display', None) or str(cliente)) if cliente else f"Cliente #{c['cliente_id']}"
                        clientes_segmento.setdefault(seg, []).append({
                            'id': c['cliente_id'],
                            'nome': nome,
                            'cpf_cnpj': getattr(cliente, 'cpf_cnpj', '') if cliente else '',
                            'recencia_dias': c['recencia_dias'],
                            'frequencia': c['frequencia'],
                            'monetario': float(c['monetario'] or 0),
                            'R': c['R'], 'F': c['F'], 'M': c['M'],
                        })

                    for lista in clientes_segmento.values():
                        lista.sort(key=lambda item: item['monetario'], reverse=True)

                    total = len(clientes)

            except Exception as exc:
                erro = str(exc)

        segmentos = []
        for d in self._RFM_DEFINICOES:
            qtd = contagem.get(d['id'], 0)
            pct = round(qtd * 100 / total) if total > 0 else 0
            segmentos.append({
                **d,
                'quantidade': qtd,
                'percentual': pct,
                'flex': max(pct, 3),
                'clientes': clientes_segmento.get(d['id'], []),
            })

        return {
            'segmentos': segmentos,
            'total_clientes': total,
            'sem_dados': total == 0,
            'erro': erro,
        }

    # ------------------------------------------------------------------
    # Vendas do dia / mês
    # ------------------------------------------------------------------

    def _vendas_periodo(self, filial, periodo: str):
        """
        Retorna métricas de vendas para 'dia' (hoje) ou 'mes' (mês corrente).
        Combina PedidoVenda (B2B) + VendaPDV (PDV).
        """
        vazio = {
            'valor_total': 0, 'qtd_pedidos': 0, 'skus_distintos': 0,
            'unidades': 0, 'media_itens': 0, 'ticket_medio': 0, 'erro': None,
        }
        if not filial:
            return vazio

        try:
            from apps.vendas.models import ItemPedidoVenda, PedidoVenda
            from apps.pdv.models import VendaPDV, ItemVendaPDV as PDVItem

            hoje = timezone.now().date()

            # --- PedidoVenda (B2B) ---
            status_validos = [
                PedidoVenda.Status.CONFIRMADO,
                PedidoVenda.Status.EM_SEPARACAO,
                PedidoVenda.Status.FATURADO,
                PedidoVenda.Status.PARCIALMENTE_FATURADO,
                PedidoVenda.Status.ENTREGUE,
            ]
            base = (
                PedidoVenda.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else PedidoVenda.objects.filter(filial=filial)
            ).filter(status__in=status_validos)
            if periodo == 'dia':
                base = base.filter(data_emissao__date=hoje)
            else:
                base = base.filter(data_emissao__year=hoje.year, data_emissao__month=hoje.month)

            agg = base.aggregate(valor_total=Sum('valor_total'), qtd_pedidos=Count('id'))
            valor_total = float(agg['valor_total'] or 0)
            qtd_pedidos = agg['qtd_pedidos'] or 0
            pedido_ids = list(base.values_list('id', flat=True))
            itens_agg = ItemPedidoVenda.objects.filter(pedido_id__in=pedido_ids).aggregate(
                skus=Count('produto_id', distinct=True),
                unidades=Sum('quantidade'),
                total_linhas=Count('id'),
            )
            skus = itens_agg['skus'] or 0
            unidades = float(itens_agg['unidades'] or 0)
            total_linhas = itens_agg['total_linhas'] or 0

            # --- VendaPDV ---
            pdv_qs = (
                VendaPDV.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else VendaPDV.objects.filter(filial=filial)
            ).filter(status='finalizada')
            if periodo == 'dia':
                pdv_qs = pdv_qs.filter(data_venda__date=hoje)
            else:
                pdv_qs = pdv_qs.filter(data_venda__year=hoje.year, data_venda__month=hoje.month)

            pdv_agg = pdv_qs.aggregate(valor_total=Sum('valor_total'), qtd_pedidos=Count('id'))
            pdv_ids = list(pdv_qs.values_list('id', flat=True))
            pdv_itens = PDVItem.objects.filter(venda_pdv_id__in=pdv_ids).aggregate(
                skus=Count('produto_id', distinct=True),
                unidades=Sum('quantidade'),
                total_linhas=Count('id'),
            )

            # --- Combina ---
            valor_total  += float(pdv_agg['valor_total'] or 0)
            qtd_pedidos  += pdv_agg['qtd_pedidos'] or 0
            skus         += pdv_itens['skus'] or 0
            unidades     += float(pdv_itens['unidades'] or 0)
            total_linhas += pdv_itens['total_linhas'] or 0

            media_itens  = round(total_linhas / qtd_pedidos, 1) if qtd_pedidos else 0
            ticket_medio = round(valor_total / qtd_pedidos, 2) if qtd_pedidos else 0

            return {
                'valor_total': valor_total,
                'qtd_pedidos': qtd_pedidos,
                'skus_distintos': skus,
                'unidades': unidades,
                'media_itens': media_itens,
                'ticket_medio': ticket_medio,
                'erro': None,
            }
        except Exception as exc:
            return {**vazio, 'erro': str(exc)}

    # ------------------------------------------------------------------
    # Vendas acumuladas por mês (últimos 6 meses)
    # ------------------------------------------------------------------

    def _vendas_acumuladas_mes(self, filial, meses=6):
        """
        Retorna dois agrupamentos dos últimos `meses` meses:
        - por_filial: [{mes_label, filial_nome, qtd, valor}]
        - por_pagamento: [{mes_label, forma, qtd, valor}]
        E uma lista de rótulos de meses para o cabeçalho.
        """
        vazio = {'por_filial': [], 'por_pagamento': [], 'meses': [], 'erro': None}
        if not filial:
            return vazio

        try:
            from apps.vendas.models import PedidoVenda
            from apps.financeiro.models import ContaReceber
            from apps.pdv.models import VendaPDV, PagamentoVendaPDV

            hoje = timezone.now().date()
            mes_inicio = hoje.replace(day=1)
            for _ in range(meses - 1):
                mes_inicio = (mes_inicio - datetime.timedelta(days=1)).replace(day=1)

            MESES_PT = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                        'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

            status_validos = [
                PedidoVenda.Status.CONFIRMADO,
                PedidoVenda.Status.EM_SEPARACAO,
                PedidoVenda.Status.FATURADO,
                PedidoVenda.Status.PARCIALMENTE_FATURADO,
                PedidoVenda.Status.ENTREGUE,
            ]

            # --- PedidoVenda por filial ---
            base = (
                PedidoVenda.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else PedidoVenda.objects.filter(filial=filial)
            ).filter(status__in=status_validos, data_emissao__date__gte=mes_inicio)

            por_filial_qs = (
                base
                .values('data_emissao__year', 'data_emissao__month',
                        'filial__nome_fantasia', 'filial__razao_social')
                .annotate(qtd=Count('id'), valor=Sum('valor_total'))
                .order_by('data_emissao__year', 'data_emissao__month')
            )

            # acumula em dict keyed by (ano, mes, filial_nome)
            acum_filial = {}
            meses_vistos = set()
            for row in por_filial_qs:
                ano, mes = row['data_emissao__year'], row['data_emissao__month']
                label = f"{MESES_PT[mes]}/{str(ano)[2:]}"
                meses_vistos.add((ano, mes, label))
                fn = row['filial__nome_fantasia'] or row['filial__razao_social'] or '—'
                k = (ano, mes, fn)
                acum_filial.setdefault(k, {'qtd': 0, 'valor': 0.0})
                acum_filial[k]['qtd']   += row['qtd']
                acum_filial[k]['valor'] += float(row['valor'] or 0)

            # --- VendaPDV por filial ---
            pdv_base = (
                VendaPDV.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else VendaPDV.objects.filter(filial=filial)
            ).filter(status='finalizada', data_venda__date__gte=mes_inicio)

            pdv_filial_qs = (
                pdv_base
                .values('data_venda__year', 'data_venda__month',
                        'filial__nome_fantasia', 'filial__razao_social')
                .annotate(qtd=Count('id'), valor=Sum('valor_total'))
                .order_by('data_venda__year', 'data_venda__month')
            )
            for row in pdv_filial_qs:
                ano, mes = row['data_venda__year'], row['data_venda__month']
                label = f"{MESES_PT[mes]}/{str(ano)[2:]}"
                meses_vistos.add((ano, mes, label))
                fn = row['filial__nome_fantasia'] or row['filial__razao_social'] or '—'
                k = (ano, mes, fn)
                acum_filial.setdefault(k, {'qtd': 0, 'valor': 0.0})
                acum_filial[k]['qtd']   += row['qtd']
                acum_filial[k]['valor'] += float(row['valor'] or 0)

            por_filial = [
                {
                    'mes_label': f"{MESES_PT[mes]}/{str(ano)[2:]}",
                    'ano': ano, 'mes': mes,
                    'filial_nome': fn,
                    'qtd': v['qtd'],
                    'valor': v['valor'],
                }
                for (ano, mes, fn), v in sorted(acum_filial.items())
            ]

            # --- Forma de pagamento: ContaReceber (B2B) + PagamentoVendaPDV (PDV) ---
            cr_base = (
                ContaReceber.objects.filter(
                    filial__empresa=filial.empresa,
                    documento_tipo='pedido_venda',
                    data_emissao__gte=mes_inicio,
                ) if filial.is_matriz else
                ContaReceber.objects.filter(
                    filial=filial,
                    documento_tipo='pedido_venda',
                    data_emissao__gte=mes_inicio,
                )
            )
            por_pgto_qs = (
                cr_base
                .values('data_emissao__year', 'data_emissao__month', 'forma_pagamento__descricao')
                .annotate(qtd=Count('documento_id', distinct=True), valor=Sum('valor_original'))
                .order_by('data_emissao__year', 'data_emissao__month')
            )

            acum_pgto = {}
            for row in por_pgto_qs:
                ano, mes = row['data_emissao__year'], row['data_emissao__month']
                label = f"{MESES_PT[mes]}/{str(ano)[2:]}"
                meses_vistos.add((ano, mes, label))
                forma = row['forma_pagamento__descricao'] or 'Não informada'
                k = (ano, mes, forma)
                acum_pgto.setdefault(k, {'qtd': 0, 'valor': 0.0})
                acum_pgto[k]['qtd']   += row['qtd']
                acum_pgto[k]['valor'] += float(row['valor'] or 0)

            # PDV pagamentos
            pdv_pgto_qs = (
                PagamentoVendaPDV.objects.filter(
                    venda_pdv__in=pdv_base
                )
                .values('venda_pdv__data_venda__year', 'venda_pdv__data_venda__month',
                        'forma_pagamento__descricao')
                .annotate(qtd=Count('venda_pdv_id', distinct=True), valor=Sum('valor'))
                .order_by('venda_pdv__data_venda__year', 'venda_pdv__data_venda__month')
            )
            for row in pdv_pgto_qs:
                ano = row['venda_pdv__data_venda__year']
                mes = row['venda_pdv__data_venda__month']
                label = f"{MESES_PT[mes]}/{str(ano)[2:]}"
                meses_vistos.add((ano, mes, label))
                forma = row['forma_pagamento__descricao'] or 'Não informada'
                k = (ano, mes, forma)
                acum_pgto.setdefault(k, {'qtd': 0, 'valor': 0.0})
                acum_pgto[k]['qtd']   += row['qtd']
                acum_pgto[k]['valor'] += float(row['valor'] or 0)

            por_pagamento = [
                {
                    'mes_label': f"{MESES_PT[mes]}/{str(ano)[2:]}",
                    'ano': ano, 'mes': mes,
                    'forma': forma,
                    'qtd': v['qtd'],
                    'valor': v['valor'],
                }
                for (ano, mes, forma), v in sorted(acum_pgto.items())
            ]

            meses_ordenados = [lbl for _, _, lbl in sorted(meses_vistos)]

            return {
                'por_filial': por_filial,
                'por_pagamento': por_pagamento,
                'meses': meses_ordenados,
                'erro': None,
            }
        except Exception as exc:
            return {**vazio, 'erro': str(exc)}

    # ------------------------------------------------------------------
    # Vendas por dia da semana
    # ------------------------------------------------------------------

    @staticmethod
    def _menos_meses(d, n):
        """Retorna a data `n` meses antes de `d`, ajustando o dia ao fim do mês."""
        import calendar
        mes = d.month - n
        ano = d.year
        while mes <= 0:
            mes += 12
            ano -= 1
        dia = min(d.day, calendar.monthrange(ano, mes)[1])
        return d.replace(year=ano, month=mes, day=dia)

    def _vendas_por_dia_semana(self, filial):
        """
        Faturamento agregado por dia da semana (Seg..Dom) em vários períodos.
        Combina PedidoVenda (B2B) + VendaPDV. Retorna dados prontos para o
        seletor de período no dashboard.
        """
        if not filial:
            return {'periodos': {}, 'erro': None}

        try:
            from django.db.models.functions import ExtractIsoWeekDay
            from apps.vendas.models import PedidoVenda
            from apps.pdv.models import VendaPDV

            hoje = timezone.localdate()
            primeiro_mes = hoje.replace(day=1)
            fim_mes_anterior = primeiro_mes - datetime.timedelta(days=1)
            inicio_mes_anterior = fim_mes_anterior.replace(day=1)

            periodos_def = {
                'mes_atual': ('Mês atual', primeiro_mes, hoje),
                'mes_anterior': ('Mês anterior', inicio_mes_anterior, fim_mes_anterior),
                'ultimos_3_meses': ('Últimos 3 meses', self._menos_meses(hoje, 3), hoje),
                'ultimos_6_meses': ('Últimos 6 meses', self._menos_meses(hoje, 6), hoje),
                'ultimo_ano': ('Último ano', self._menos_meses(hoje, 12), hoje),
            }

            status_validos = [
                PedidoVenda.Status.CONFIRMADO,
                PedidoVenda.Status.EM_SEPARACAO,
                PedidoVenda.Status.FATURADO,
                PedidoVenda.Status.PARCIALMENTE_FATURADO,
                PedidoVenda.Status.ENTREGUE,
            ]
            pedido_base = (
                PedidoVenda.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else PedidoVenda.objects.filter(filial=filial)
            ).filter(status__in=status_validos)
            pdv_base = (
                VendaPDV.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else VendaPDV.objects.filter(filial=filial)
            ).filter(status='finalizada')

            labels = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

            def _buckets(qs, data_field, inicio, fim):
                filtro = {f'{data_field}__date__gte': inicio, f'{data_field}__date__lte': fim}
                rows = (
                    qs.filter(**filtro)
                    .annotate(dow=ExtractIsoWeekDay(data_field))
                    .values('dow')
                    .annotate(total=Sum('valor_total'))
                )
                acc = {i: 0.0 for i in range(1, 8)}
                for r in rows:
                    if r['dow']:
                        acc[int(r['dow'])] += float(r['total'] or 0)
                return acc

            periodos = {}
            for chave, (label, inicio, fim) in periodos_def.items():
                b = _buckets(pedido_base, 'data_emissao', inicio, fim)
                p = _buckets(pdv_base, 'data_venda', inicio, fim)
                dias = []
                total = 0.0
                for i in range(1, 8):
                    valor = round(b[i] + p[i], 2)
                    total += valor
                    dias.append({'dia': labels[i - 1], 'valor': valor})
                maximo = max((d['valor'] for d in dias), default=0.0)
                periodos[chave] = {
                    'label': label,
                    'dias': dias,
                    'total': round(total, 2),
                    'maximo': maximo,
                }

            return {'periodos': periodos, 'erro': None}
        except Exception as exc:
            return {'periodos': {}, 'erro': str(exc)}

    # ------------------------------------------------------------------
    # Curva ABC — helpers comuns
    # ------------------------------------------------------------------

    @staticmethod
    def _classificar_abc(itens_ordenados, campo_valor):
        """
        Recebe lista de dicts ordenada por `campo_valor` desc.
        Adiciona 'classe' (A/B/C), 'pct_receita' e 'pct_acumulado' a cada item.
        Retorna (itens_com_classe, resumo_por_classe).
        Limites: A=80 %, B=95 %, C=100 %.
        """
        total = sum(float(r[campo_valor] or 0) for r in itens_ordenados)
        if total == 0:
            return [], {}

        acumulado = 0
        resumo = {'A': {'qtd': 0, 'receita': 0}, 'B': {'qtd': 0, 'receita': 0}, 'C': {'qtd': 0, 'receita': 0}}

        for item in itens_ordenados:
            valor = float(item[campo_valor] or 0)
            acumulado += valor
            pct_acum = acumulado / total * 100

            if pct_acum <= 80:
                classe = 'A'
            elif pct_acum <= 95:
                classe = 'B'
            else:
                classe = 'C'

            item['classe'] = classe
            item['pct_receita'] = round(valor / total * 100, 1)
            item['pct_acumulado'] = round(pct_acum, 1)
            resumo[classe]['qtd'] += 1
            resumo[classe]['receita'] += valor

        for cls in resumo:
            resumo[cls]['pct_receita'] = round(resumo[cls]['receita'] / total * 100, 1)

        return itens_ordenados, resumo

    def _base_qs_vendas(self, filial):
        """QuerySet base de PedidoVenda filtrado por filial/empresa e status válidos."""
        from apps.vendas.models import PedidoVenda

        status_validos = [
            PedidoVenda.Status.CONFIRMADO,
            PedidoVenda.Status.EM_SEPARACAO,
            PedidoVenda.Status.FATURADO,
            PedidoVenda.Status.PARCIALMENTE_FATURADO,
            PedidoVenda.Status.ENTREGUE,
        ]
        hoje = timezone.now().date()
        inicio = hoje - datetime.timedelta(days=365)

        base = (
            PedidoVenda.objects.filter(filial__empresa=filial.empresa)
            if filial.is_matriz
            else PedidoVenda.objects.filter(filial=filial)
        )
        return base.filter(status__in=status_validos, data_emissao__date__gte=inicio)

    # ------------------------------------------------------------------
    # Curva ABC — Clientes
    # ------------------------------------------------------------------

    def _curva_abc_clientes(self, filial, top=12):
        if not filial:
            return {'itens': [], 'resumo': {}, 'sem_dados': True, 'erro': None}

        erro = None
        try:
            from apps.pdv.models import VendaPDV

            # --- PedidoVenda (B2B) ---
            b2b_qs = (
                self._base_qs_vendas(filial)
                .filter(cliente_id__isnull=False)
                .values('cliente_id', 'cliente__razao_social', 'cliente__nome_fantasia')
                .annotate(receita=Sum('valor_total'))
            )

            # --- VendaPDV com cliente identificado ---
            hoje = timezone.now().date()
            inicio = hoje - datetime.timedelta(days=365)
            pdv_qs = (
                (
                    VendaPDV.objects.filter(filial__empresa=filial.empresa)
                    if filial.is_matriz
                    else VendaPDV.objects.filter(filial=filial)
                )
                .filter(status='finalizada', cliente_id__isnull=False, data_venda__date__gte=inicio)
                .values('cliente_id', 'cliente__razao_social', 'cliente__nome_fantasia')
                .annotate(receita=Sum('valor_total'))
            )

            # --- Combina ---
            acum = {}
            for row in list(b2b_qs) + list(pdv_qs):
                cid = row['cliente_id']
                acum.setdefault(cid, {
                    'cliente_id': cid,
                    'cliente__razao_social': row['cliente__razao_social'],
                    'cliente__nome_fantasia': row['cliente__nome_fantasia'],
                    'receita': 0.0,
                })
                acum[cid]['receita'] += float(row['receita'] or 0)

            itens = sorted(acum.values(), key=lambda x: x['receita'], reverse=True)
            if not itens:
                return {'itens': [], 'resumo': {}, 'sem_dados': True, 'erro': None}

            itens_cls, resumo = self._classificar_abc(itens, 'receita')
            for i, item in enumerate(itens_cls, start=1):
                item['rank'] = i
                item['nome'] = item['cliente__nome_fantasia'] or item['cliente__razao_social'] or f'Cliente {item["cliente_id"]}'

            return {
                'itens': itens_cls[:top],
                'total_itens': len(itens_cls),
                'resumo': resumo,
                'sem_dados': False,
                'erro': None,
            }
        except Exception as exc:
            erro = str(exc)
            return {'itens': [], 'resumo': {}, 'sem_dados': True, 'erro': erro}

    # ------------------------------------------------------------------
    # Curva ABC — Produtos
    # ------------------------------------------------------------------

    def _curva_abc_produtos(self, filial, top=12):
        if not filial:
            return {'itens': [], 'resumo': {}, 'sem_dados': True, 'erro': None}

        erro = None
        try:
            from apps.vendas.models import ItemPedidoVenda
            from apps.pdv.models import ItemVendaPDV as PDVItem, VendaPDV

            # --- PedidoVenda (B2B) ---
            pedidos_ids = self._base_qs_vendas(filial).values_list('id', flat=True)
            b2b_qs = (
                ItemPedidoVenda.objects.filter(pedido_id__in=pedidos_ids)
                .values('produto_id', 'produto__descricao', 'produto__codigo')
                .annotate(receita=Sum('valor_total'), quantidade=Sum('quantidade'))
            )

            # --- VendaPDV ---
            hoje = timezone.now().date()
            inicio = hoje - datetime.timedelta(days=365)
            pdv_ids = (
                VendaPDV.objects.filter(filial__empresa=filial.empresa)
                if filial.is_matriz
                else VendaPDV.objects.filter(filial=filial)
            ).filter(status='finalizada', data_venda__date__gte=inicio).values_list('id', flat=True)

            pdv_qs = (
                PDVItem.objects.filter(venda_pdv_id__in=pdv_ids)
                .values('produto_id', 'produto__descricao', 'produto__codigo')
                .annotate(receita=Sum('valor_total'), quantidade=Sum('quantidade'))
            )

            # --- Combina em dict ---
            acum = {}
            for row in list(b2b_qs) + list(pdv_qs):
                pid = row['produto_id']
                acum.setdefault(pid, {
                    'produto_id': pid,
                    'produto__descricao': row['produto__descricao'],
                    'produto__codigo': row['produto__codigo'],
                    'receita': 0.0,
                    'quantidade': 0.0,
                })
                acum[pid]['receita']    += float(row['receita'] or 0)
                acum[pid]['quantidade'] += float(row['quantidade'] or 0)

            itens = sorted(acum.values(), key=lambda x: x['receita'], reverse=True)
            if not itens:
                return {'itens': [], 'resumo': {}, 'sem_dados': True, 'erro': None}

            itens_cls, resumo = self._classificar_abc(itens, 'receita')
            for i, item in enumerate(itens_cls, start=1):
                item['rank'] = i
                item['nome'] = item['produto__descricao'] or f'Produto {item["produto_id"]}'
                item['codigo'] = item['produto__codigo'] or ''

            return {
                'itens': itens_cls[:top],
                'total_itens': len(itens_cls),
                'resumo': resumo,
                'sem_dados': False,
                'erro': None,
            }
        except Exception as exc:
            erro = str(exc)
            return {'itens': [], 'resumo': {}, 'sem_dados': True, 'erro': erro}

    @staticmethod
    def _segmento_rfm(R, F, M):
        if R >= 4 and F >= 4 and M >= 4:
            return 'campeoes'
        if R >= 3 and F >= 3 and M >= 4:
            return 'vips'
        if R >= 4 and F == 1:
            return 'recentes'
        if R >= 3 and F >= 2 and M >= 3:
            return 'potenciais'
        if R >= 3 and F == 1 and M >= 2:
            return 'promissores'
        if R == 1 and F >= 4 and M >= 4:
            return 'nao_perder'
        if R <= 2 and F >= 3 and M >= 3:
            return 'em_risco'
        if 2 <= R <= 3 and 2 <= F <= 3 and 2 <= M <= 3:
            return 'precisam_atencao'
        if 1 < R <= 2 and F <= 2 and M <= 2:
            return 'prestes_dormir'
        if R == 1 and F >= 2 and M >= 2:
            return 'hibernando'
        return 'perdidos'
